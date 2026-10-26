# Extension ↔ server contract

The extension finds manga page images, fetches their bytes, and sends them to a local server. The server detects text, OCRs it, translates it, inpaints the original text away, typesets English, and returns a rendered page. The extension swaps the rendered page into the `<img>` it came from.

The server never fetches URLs. Only the extension touches the network for page images, because only the browser has the site's cookies and passes its bot checks.

## Server

Base URL: `http://127.0.0.1:7878`. CORS allows `chrome-extension://*` origins for `GET`, `POST` and `DELETE`.

### `GET /v1/health`

```json
{ "ok": true, "version": "0.1.0", "translator": "openai:google/gemma-4-12b", "gpu": "NVIDIA GeForce RTX 5070 Ti" }
```

### `POST /v1/pages`

Multipart form:

| Field | Type | Meaning |
| --- | --- | --- |
| `image` | file | Original page bytes (webp, jpeg, png). |
| `context_key` | string, optional | Groups pages of one work (for example `example.com/g/12345`). The translator sees earlier pages' dialogue with the same key, so names and tone stay consistent. |
| `page_index` | int, optional | Order within the context. Used to pick which earlier pages are context. |

Response `200 application/json`, a `TranslatedPage`:

```json
{
  "page_id": "sha256 hex of the input bytes",
  "width": 1280,
  "height": 1808,
  "rendered_url": "/v1/pages/<page_id>/rendered.webp",
  "bubbles": [
    {
      "box": [x0, y0, x1, y1],
      "source": "原文",
      "translation": "Original text",
      "vertical": true
    }
  ],
  "timings_ms": { "detect": 41, "balloons": 15, "ocr": 280, "translate": 2100, "inpaint": 190, "typeset": 30, "encode": 90, "total": 2750 },
  "cached": false
}
```

Boxes are in pixels of the original image. Re-posting identical bytes returns the cached result with `"cached": true` and near-zero timings. The cache lives on disk under `server/cache/v<pipeline version>/` keyed by `page_id`, so a server whose rendering changed re-renders pages instead of serving old results. At startup the server deletes the folders of older pipeline versions and the unversioned `<page_id>` folders of the first cache layout, and nothing else. The current version's cache is capped at `YOMIKAE_CACHE_MAX_BYTES`, 2 GiB by default. After a newly rendered page is written, a background task evicts the least recently read pages until the cache fits again. Every cache hit counts as a read. The page just written and pages still being written are never evicted. An evicted page drops out of the translation memory, names and context of its work, exactly as if the server had restarted without it, and is re-rendered the next time it is posted.

A page with no text returns `bubbles: []` and `rendered_url` pointing at an unchanged copy.

### `GET /v1/pages/{page_id}/rendered.<ext>`

The final page. Same pixel size as the input. Always fetch it through `rendered_url`, whose extension names the format. A page whose sides are both at most 16383 px, WebP's limit, is `rendered.webp` (`image/webp`). A taller or wider page, such as a long vertical strip, is `rendered.jpg` (`image/jpeg`, quality 90), and one with a side over 65535 px is `rendered.png` (`image/png`). The `Content-Type` header matches. Any other extension for the page answers `404 not_found`, as does a page that has been evicted or cleared.

Strips and spreads more than twice as long as they are wide are read in overlapping tiles 1.5 times as long as the short side, so their text is not shrunk to fit one detector input. Each bubble is reported once, in full-image pixels. A bubble no longer than half the short side is always found whole even where it crosses a tile edge.

### `DELETE /v1/cache`

Clears the current pipeline version's cache and drops those pages from the translation memory. Pages still being written stay. Response `200 application/json`:

```json
{ "removed": 42 }
```

### `POST /v1/reports`

Saves everything needed to reproduce a problem with one page to a local folder. The extension sends this when the reader reports a page, which works whether the page translated, failed, or is still translating.

Multipart form:

| Field | Type | Meaning |
| --- | --- | --- |
| `image` | file | Original page bytes, the same bytes sent to `/v1/pages`. |
| `page_url` | string | URL of the reader page the image was on. |
| `image_url` | string | URL of the page image. |
| `context_key` | string, optional | As in `/v1/pages`. |
| `page_index` | int, optional | As in `/v1/pages`. |
| `note` | string, optional | What the reader says is wrong. |
| `job_state` | string, optional | The extension's state for the page (`queued`, `fetching`, `translating`, `waiting`, `ready`, `failed`, `blocked`, or `untracked` for a page with no job). |
| `job_error` | string, optional | The error the extension showed, for a failed page. |
| `areas` | JSON string, optional | The parts of the page the reader marked: `[{"box": [x0, y0, x1, y1], "bubble_index": 2 \| null}]`. Boxes are fractions (0 to 1) of the displayed image, so they do not depend on how large the site drew it. `bubble_index` is set when the reader clicked a detected bubble. Malformed JSON returns `400 bad_request`. |

The server writes one directory per report under `~/.local/share/yomikae/reports/` (override with `YOMIKAE_REPORTS_DIR`), outside the repo so reports survive worktrees and clones. The directory name is the report id, `<UTC timestamp>-<first 12 hex of page_id>`, for example `20261025T005706.775Z-c1c01c3266d7`. It is written to a temporary dot-directory and renamed into place, so a reader never sees a partial report.

| File | Present | Contents |
| --- | --- | --- |
| `original.<ext>` | always | The bytes from `image`. The extension comes from the image format (`jpg`, `png`, `webp`, `gif`, `avif`, `bmp`, the lowercase Pillow name of another format, `svg`, or `bin`). |
| `result.json` | when the page is in the cache for the current pipeline version | The cached `TranslatedPage` with bubbles, plus `context_key`, `page_index`, `font_sizes`, and `skipped`. |
| `rendered.<ext>` | with `result.json` | The rendered page from the cache, under the same name as in its `rendered_url`. |
| `marked.png` | when areas were marked and the original decodes | The original page with each area outlined in magenta and labelled `bubble <i>` or `area <n>`. |
| `report.json` | always | `page_url`, `image_url`, `context_key`, `page_index`, `page_id`, `pipeline_version`, `note`, `job_state`, `job_error`, `areas`, `areas_units`, `created_at`, and the names of the other files: `original`, `result` (`"result.json"` or `null`), `rendered` (`"rendered.webp"`, `"rendered.jpg"`, `"rendered.png"`, or `null`), `marked` (`"marked.png"` or `null`). `areas` is `[{"box": [x0, y0, x1, y1], "bubble_index": int | null}]` in original image pixels, converted with the decoded image's real size. A snapped area takes the exact box of that bubble in `result.json`, and `bubble_index` is kept only when `result.json` has that bubble. `areas_units` is `"pixels"`, or `"fraction"` when the original could not be decoded. |

Response `200 application/json`:

```json
{ "report_id": "20261025T005706.775Z-c1c01c3266d7", "path": "/Users/me/.local/share/yomikae/reports/20261025T005706.775Z-c1c01c3266d7" }
```

### Errors

Non-2xx responses return `{ "error": "<code>", "detail": "<human text>" }`. Codes: `bad_image`, `bad_request`, `not_found`, `translator_unavailable`, `translator_error`, `internal`. A request with missing or malformed fields also gets `bad_image`. `translator_error` (500) means the translator answered but refused the request, for example a 400. The server resends a 400 whose body contains `Channel Error` or `predict request failed: fetch failed`, which LM Studio returns now and then for a request that succeeds when sent again, up to twice, after 0.5 s and then 1 s, before it answers `translator_error`. If the translator is down, the server still answers `/v1/health` with `"ok": false` and a `detail`.

## Extension page state

Each page image the extension tracks is one `PageJob`, keyed by the image's source URL:

```ts
type PageJob =
  | { state: 'queued'; url: string; priority: number }
  | { state: 'fetching'; url: string }
  | { state: 'translating'; url: string }
  | { state: 'waiting'; url: string; detail: string }
  | { state: 'ready'; url: string; page: TranslatedPage; renderedBlobUrl: string }
  | { state: 'failed'; url: string; error: string; retryable: boolean }
  | { state: 'blocked'; url: string; host: string }
```

Priority is distance from the page the reader is looking at, so the current page goes first and the next pages after it. At most 2 jobs are in flight at once.

A job's image is fetched from a list of candidate URLs, best first, for example the original resolution and then smaller sizes, or the same file on other mirrors. A candidate that answers 404 or 410 moves on to the next one. The job fails without Retry only when every candidate was missing, or when it was refused.

The background worker refuses a `want` or a report whose candidates include an address on this computer (`localhost`, a host under `.localhost`, `127.0.0.0/8`, `0.0.0.0` or `[::1]`), unless the tab that sent it is on this computer too. Fetches send the browser's cookies, so without this a page on an enabled site could make the extension fetch from local services and hand the result back to it. A refused job fetches nothing and ends in `failed` without Retry, with an error such as "Refused to fetch 127.0.0.1: a web page cannot load images from this computer." A refused report fails with the same message.

A job that fails because the server is unavailable moves to `waiting` instead of `failed`. Unavailable means the connection failed, the rendered page could not be fetched, or the server answered 502, 503 or 504, which includes `translator_unavailable`. While any job waits, the background worker sends no `POST /v1/pages`. It moves every queued job to `waiting` and polls `GET /v1/health` after 2, 4, 8 and 16 seconds, then every 30 seconds. When health answers `"ok": true`, the waiting jobs go back to `queued` and run in their usual priority order, reusing the image bytes they already fetched. The chip reads "Waiting for server…", and its tooltip gives the last health detail. Polling stops when no tab still wants a waiting job. A request that times out, `bad_image`, and other errors on one page still end in `failed`, with Retry and Report on the chip.

A job ends in `blocked` when an image fetch fails and the extension has no host permission for that image's host, which also keeps the Referer rule from applying. The chip reads "No access to <host>. Open the Yomikae popup and click Allow access." instead of the generic failure, and keeps Report. The background worker records that host as an image host of the site of each tab that wanted the job (see Enabled sites). A site stays enabled while its own pattern is granted, even when one of its adapter's extra image origins or recorded image hosts is not. The popup shows such a site as on, reads "Images on this site come from <hosts>." listing every missing host, and offers one **Allow access** button that requests only the missing origins. Nothing is requested before that click. When any origin is granted, the background worker tells every tab, and blocked jobs go back to `queued` without a reload. `POST /v1/reports` requires the image, so a report from a blocked page fails with the same message.

## Enabled sites

A site is enabled when the reader turns it on in the popup or grants its pattern, for example `*://*.x.com/*`, in Chrome's site access. The pattern covers the page's registrable domain, the name under its public suffix in the [Public Suffix List](https://publicsuffix.org/), including private suffixes. So `www.bbc.co.uk` gives `*://*.bbc.co.uk/*` and `someone.github.io` gives `*://*.someone.github.io/*`. An IP address, a host without a dot, or a host that is itself a public suffix gives `<protocol>//<host>/*`. Each enabled site is stored in `chrome.storage.local` under its own key, `site:<site>`, with its pattern as the value, so two sites enabled at once never overwrite each other. Turning a site off in the popup removes the key, the content script and the grant.

Most sites serve their images from another domain, for example discord.com from media.discordapp.net and cdn.discordapp.com, which the site's pattern does not cover. An adapter can name such origins up front as extra image origins. For any other host, the first job on the site that ends `blocked` records it under `imageHosts:<site>` as a list of host names, for example `["media.discordapp.net"]`. Hosts are recorded only for an enabled site, and only while its `site:` key exists. Each host is granted as its exact origin, `*://<host>/*`, the same form the adapters' extra image origins use which covers the origin the worker checks before calling a job blocked. A wildcard over the registrable domain would grant every subdomain of a CDN on the evidence of one. A site's origins are its pattern, its adapter's extra image origins and its recorded image hosts. Turning the site off also removes the `imageHosts:` key and the grants of its origins, except an origin another enabled site still uses, such as `*://pbs.twimg.com/*` while x.com or twitter.com stays on.

Chrome drops the extension's registered content scripts whenever the extension is updated or reloaded, including from the reload button in `chrome://extensions`, even though its host permissions stay granted. So on install, update, reload and browser start, the background worker registers the content script again for every stored site whose pattern is still granted, and injects it into that site's open tabs. A stored site whose pattern was revoked is forgotten and stays off, along with its recorded image hosts and their grants. So is a stored site whose pattern covers a whole public suffix, such as `*://*.co.uk/*` from a version that took the last two labels of the host, and its grants are given back. Recorded image hosts and their grants survive a reload unchanged, so a site comes back with the image hosts it had. Running this twice leaves one registration per site.

A profile from before sites were stored has no `site:` keys. The first update or start of such a profile treats every granted pattern that names a site as enabled, once, and records `sitesSeeded` so it never guesses again. The adapters' extra image origins, such as `*://i.pximg.net/*`, and recorded image hosts never become sites, whether granted from the popup, from Chrome's site access, or found by that recovery.

The manifest's server hosts, `http://127.0.0.1/*` and `http://localhost/*`, never become sites from a grant or from that recovery. A reader site served from `127.0.0.1` can still be turned on in the popup like any other.

## Site modes

Each enabled site translates in one of two modes, stored per site in `chrome.storage.local` under `siteModes` (`{ "<site>": "auto" | "on-demand" }`) and chosen in the popup. A change applies to open tabs at once, and jobs already queued still finish.

| Mode | What is sent to the server |
| --- | --- |
| `auto` | Every page image near the viewport, plus the next pages the site adapter predicts. |
| `on-demand` | Nothing until the reader hovers an image and clicks **Translate** in its chip. That image is queued like any other job. After that, the adapter may queue the next pages of the same work or post, and keeps doing so while the reader scrolls through pages of it that already have jobs. |

An image that was already translated in this browser session shows its translation on any page without a click, and without a new request to the server. The background worker answers from its result cache.

x.com, twitter.com, pixiv.net and every other site default to `on-demand`. An adapter can set `auto` as its default instead. In `on-demand` mode the Translate chip appears on images the adapter recognises as pages, or on images at least 200 px on each side when the site has no adapter, so avatars, emoji and icons never offer it.

### Skipping a work

An adapter can name the work on the tab with `workId(location)`, such as `pixiv.net/artworks/<id>`, and can say what language the work is already in with `workLanguage(location)`, a lowercase English name such as `english` or `japanese`. The content script reads both before it starts, and waits at most 3 seconds for the language. A page whose adapter has no `workId`, or whose address names no work, translates by its site mode alone.

The reader's choice for a work is stored in `chrome.storage.local` under its own key, `work:<work id>`, with the value `"on"` or `"off"`. One key per work keeps two tabs from overwriting each other. The key exists only while the choice differs from the default. A work in English is off by default, and any other work is on.

The tab's mode combines the site mode and the work:

```ts
type TabMode = SiteMode | 'off'
```

It is `off` when the work's choice is `"off"`, or when no choice is stored and the language is `english`. Otherwise it is the site mode. While the tab is `off`:

- The content script tracks no image, offers no **Translate** chip, asks the background worker for no cached result, and sends no `want`.
- Pages already swapped go back to the original, and their translations stay in memory.
- Jobs that are not `ready` are forgotten, and the content script sends `{ type: 'drop', keys }`. The background worker removes the tab from those jobs and deletes each job no other tab wants, unless it is already `translating`. A job deleted while its image is being fetched ends after the fetch, without a `POST /v1/pages`.

Turning the work back on rescans the page. Pages translated earlier show at once, and the rest are translated by the site mode. A change applies to every open tab of the work, and a reload keeps it. When the address moves to another work without a reload, the tab is `off` until that work's choice and language are read.

The popup asks the tab for its work with the tab command `{ type: 'work' }`, which the content script answers with `{ id, language, choice }` or `null`. While the site is on and the tab names a work, the popup shows **Translate this gallery**. For a work that is off because it is in English, it also reads "Already in English, not translating." with a **Translate anyway** button, which stores `"on"`.

x.com keys an image as `x:<media id>`, so every size of one image shares one job, and groups context by tweet (`x.com/i/status/<id>`), the thread on a status page or the enclosing tweet on a timeline. X draws each photo as a background image on a DIV next to a transparent IMG, so the swap and the Alt peek change that DIV too. The photo view takes its page number from `/photo/<n>` in the URL. pixiv keys a page as `pixiv:<artwork id>_p<n>`, groups context by artwork (`pixiv.net/artworks/<id>`) and fetches the original named by `/ajax/illust/<id>/pages`, falling back to `master1200`. i.pximg.net refuses requests without a pixiv Referer, so the background worker installs a session `declarativeNetRequest` rule that sets `Referer: https://www.pixiv.net/` on its own requests (tab id -1) to that host.

## Reporting a page

Hovering a page image shows a small chip in its top right corner with a **Report** item. Pages that are still translating or that failed show the item next to their status. `Alt+R` reports the page under the cursor, or the page most in view when the cursor is not over one. Both start pick mode on that image. Detected bubbles show as dashed outlines. Clicking snaps to the nearest bubble and highlights it, shift-click adds another, and clicking a highlighted bubble removes it. Dragging adds a free rectangle, for text the detector missed. Pick mode catches every click on the image, so the site's click-to-advance never fires, and it is drawn in an overlay, so nothing shifts. A one-line note box sits in the chip. Enter or Send sends the report, Esc cancels, and sending with nothing marked reports the whole page. A toast confirms with the report id, or shows why the report failed.

The background worker keeps the original bytes of the last 40 fetched pages, so a report re-sends exactly what was translated. If the bytes were evicted or the worker restarted, it fetches the image again.
