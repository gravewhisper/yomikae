# Writing a site adapter

A site adapter tells the extension which images on one site are manga pages, which URLs to fetch for each page, how pages group into one work, and which pages come next. Sites without an adapter use the default adapter, which [sites.md](sites.md) describes along with the built-in adapters.

This guide covers adapters you build into your own copy of the extension. This repo accepts adapters only for the platforms the [contributor rules](../AGENTS.md#site-adapters) allow.

## How the build finds adapters

Chrome extensions cannot load code from the network, so every adapter is compiled into the extension when you run `pnpm build`. The build looks in two folders:

- `extension/adapters.local/`, which git ignores.
- The folder named by the `YOMIKAE_ADAPTERS` environment variable, if it is set. A relative path is resolved from the directory you run the command in. The build stops with an error if the folder does not exist.

In each folder, every top-level `.ts` file is one adapter, except files that end in `.test.ts` or `.d.ts`. The file's default export is the adapter. The build does not look in subfolders, so put shared helpers and scripts in a subfolder.

The build lists what it found:

```text
built dist (local adapters: mysite, othersite)
```

With neither folder present, the build contains only the built-in adapters. Local adapters come after the built-in ones, and the first adapter whose `matches` returns `true` for a host handles that site. A local adapter therefore cannot replace a built-in adapter for the same host.

After you change an adapter, run `pnpm build` again and reload the extension in `chrome://extensions`.

## Start from the template

`extension/adapters/template.ts` is a complete adapter for an imaginary reader at `reader.example`, with its images on `reader-cdn.example`. It sets every field, and `extension/adapters/template.test.ts` tests it.

1. Copy both files into `extension/adapters.local/`, renamed for your site.
2. Change the host names, the URL patterns, and the key prefix.
3. Delete the optional fields your site does not need.
4. Run `pnpm test`, `pnpm typecheck`, and `pnpm build` from `extension/`.

Import shared types and helpers from `#adapter`, which points at `extension/src/adapter.ts`:

| Export | What it is |
| --- | --- |
| `SiteAdapter` | The adapter type. |
| `SiteMode` | `'auto'` or `'on-demand'`. |
| `Upcoming` | `{ url, pageIndex }`, one entry in the result of `prefetch`. |
| `ImageReferer` | `{ domain, referer }`, the type of `referer`. |
| `defaultAdapter` | The adapter for sites without one. Call `defaultAdapter.contextKey(loc)` when an address is not a reader page. |
| `PREFETCH_AHEAD` | The usual number of pages to prefetch, 5. |

Adapters run in the content script on the site's tab. `document` and `location` are the site's page, and a `fetch` to a path on the site sends the reader's cookies.

## SiteAdapter fields

| Field | Required | Meaning |
| --- | --- | --- |
| `matches(host)` | Yes | Returns `true` for every host name of the site, such as `reader.example` and `www.reader.example`. |
| `defaultMode` | Yes | The mode a site starts in until the reader changes it in the popup. `'auto'` translates every page near the viewport. `'on-demand'` waits for a click on **Translate**. |
| `contextKey(loc, img?, url?)` | Yes | Names the work that the page belongs to, such as a chapter. The translator sees earlier pages with the same key as context, and reuses names from them. `loc` is the tab's address, `img` the image element when there is one, and `url` the image URL. Return the same key for every page of one work. |
| `workId(loc)` | No | Names the work the tab shows, such as a gallery or chapter, or returns `undefined` when the address is not part of one. The popup offers **Translate this gallery** only on such an address, and the reader's choice is stored under this id. Use the same string as `contextKey` for that address. |
| `workLanguage(loc)` | No | Returns a promise of the language the work is already in, as a lowercase English name such as `'english'` or `'japanese'`, or `undefined` when the site does not say. A work in `'english'` is not translated until the reader asks for it. The extension waits at most 3 seconds for the answer and calls it only when `workId` names a work. |
| `pageKey(url)` | Yes | Names one page. Two image URLs with the same key are one page and one job, so give every size and every mirror of a page the same key. Prefix it with a short name for your adapter, so it never matches another adapter's key. Return `url` unchanged for an image that is not a page. |
| `fetchCandidates(url)` | Yes | The URLs to try for a page, best first, such as the full-size original before the shown URL. It can return a promise. A candidate that answers 404 or 410 moves on to the next one. [sites.md](sites.md#fetching-a-page) describes the rest of the fetch rules. |
| `isPage(url)` | No | Returns `true` for the URLs of page images. With it, only those images are pages, and the **Translate** chip appears on page images of at least 100 px on each side. Without it, the extension guesses from the image size, as for the default adapter. |
| `pageIndex(url, img?)` | Yes | The page number inside the work, used to order jobs and to give the translator the pages before this one. Return `undefined` when the URL does not say. The extension then counts the image's position among the pages on screen. |
| `prefetch(url, loc, img?)` | No | Returns the pages after this one to translate ahead of the reader, as `Upcoming[]`. It runs when the reader clicks **Translate**, and whenever another page becomes the one most in view, in `auto` mode or when that page already has a job. Each entry needs a URL that `pageKey` keys like the page will be keyed when it is shown, or the reader gets the page translated twice. |
| `fallback(url)` | No | Returns another URL to try for a page after its fetch failed with an error that allows **Retry**, such as a timeout. It runs at most once per page. Return `undefined` when there is no other URL. |
| `extraOrigins` | No | Match patterns for hosts other than the site's own domain, such as an image host. Turning the site on in the popup asks for these as well as `*://*.<domain>/*`. |
| `referer` | No | `{ domain, referer }`. The background worker sets `Referer: <referer>` on its own requests to `domain` and its subdomains. Use it for an image host that refuses requests without the site's `Referer`. Include that host in `extraOrigins` too. |

## Test an adapter

### Unit tests

Put tests next to the adapter as `<name>.test.ts`. `pnpm test` runs them with the built-in tests, using `node:test`. Import the adapter from its own file, not through `adapterFor`, because a plain Node run does not add local adapters to the registry. For a test that calls `fetch` or reads `document` or `location`, replace those globals in the test and restore them after each test, as `template.test.ts` does.

`pnpm typecheck` checks `extension/adapters.local/` but not test files and not a folder named only by `YOMIKAE_ADAPTERS`.

### Mock pages in the browser test

`tools/e2e-sites.mjs` loads the extension in Chromium and drives copies of real sites served by `tools/sites-mock.mjs`. A local adapter can add its own mock site with a module next to it named `<name>.mock.mjs`. The module exports:

| Export | Meaning |
| --- | --- |
| `hosts` | The host names the mock serves. Chromium resolves them to the mock server. |
| `permissions` | Match patterns to grant the test build at install, such as the site's `*://*.<domain>/*` and its `extraOrigins`. |
| `handle(url, req, res, kit)` | Answers a request and returns `true`, or returns `false` to leave it to the other routes. `url` is a `URL`. `kit.send(res, status, type, body, headers)` writes the response and returns `true`. `kit.svg(width, height, label, hue)` draws a test page, and `kit.hueOf(text)` picks a hue from a string. |
| `checks(t)` | Optional. An async function that `e2e-sites.mjs` runs after the built-in checks, with the same browser and servers. |

`checks` receives one object with these fields:

- `check(ok, label, detail)` records a result.
- `open(url, ready)` opens a tab and waits until the expression `ready` is true in it.
- `enable(sw, extId, url)` turns the site on in the popup and returns the popup session.
- `popup(url)` opens the popup for the first tab whose address starts with `url` and returns its session, without changing anything.
- `posted(contextKey)` lists the page numbers the mock server was asked to translate for a work.
- `siteRequests()` lists every request the mock site received, with its host, path, `Referer`, and status.
- `rawStatus(host, path, headers)` returns the status code of one request to the mock site.
- `sw` and `extId` are the extension's service worker session and id.
- `out` is the output directory for screenshots.
- `sleep(ms)` waits.

Run the test from `extension/` with a new output directory:

```sh
node tools/e2e-sites.mjs "$(mktemp -d)"
```

The mock server answers over HTTPS with a self-signed certificate, and its pages must not use real manga images. Draw pages with `kit.svg`.

### Live sites

A script that loads the live site belongs in a subfolder of your adapter folder, so the build does not treat it as an adapter. It can import `tools/cdp.mjs` and start `tools/mock-server.mjs` with relative paths, as the scripts in `extension/tools/` do. Build the extension it loads with `ML_EXTRA_HOSTS` set to the site's patterns, because a headless run cannot answer Chrome's permission prompt.

## Keep adapters in a separate repo

You can keep adapters in a repo of your own and build them into this extension:

```text
my-adapters/
  mysite.ts
  mysite.test.ts
  mysite.mock.mjs
  tools/
    e2e-mysite-live.mjs
```

There are two ways to point the build at it:

- Clone or link it as `extension/adapters.local`. The build, `pnpm test`, `pnpm typecheck`, and `e2e-sites.mjs` find it with no other setup.
- Set `YOMIKAE_ADAPTERS` to its path for each command, for example `YOMIKAE_ADAPTERS=~/my-adapters pnpm build`. The build, `pnpm test`, and `e2e-sites.mjs` read it.

Either way, an import of `#adapter` resolves to this checkout's `extension/src/adapter.ts`, so your repo needs no copy of it. A relative import of `tools/cdp.mjs` works only when your repo is cloned at `extension/adapters.local`, not linked there, because Node resolves a script's path through the link.
