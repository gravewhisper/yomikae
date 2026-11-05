# Security

## Report a vulnerability

Please don't open a public issue for a security problem. Report it privately on GitHub instead: open the repository's **Security** tab and click **Report a vulnerability**. Only the maintainer sees the report.

Include what you found, how to reproduce it, and what an attacker could do with it. You should get a first reply within a week.

## What counts

Yomikae runs on your own machine. The server listens on `127.0.0.1` only, and the extension talks to that address. These are in scope:

- A web page that can reach the local server, or make it read or write files, outside the extension's normal use.
- The extension leaking page images, cached translations or settings to a site, or running site code with its own privileges.
- A crafted image or response that makes the server run code or write outside its cache and report folders.

These are out of scope:

- Problems that need someone to already control your machine or your browser profile.
- The translations themselves, such as a wrong or offensive line.
- Vulnerabilities in a model file, LM Studio or another tool Yomikae calls. Report those to their own projects.

## What the server and the extension guard against

The server listens on `127.0.0.1` and accepts requests only from the Yomikae extension and from tools on your own computer. Web pages you visit cannot use it, even though they run in the same browser:

- A request whose `Origin` is a web page, such as a `fetch` or a form a site submits in the background, gets 403. So does an `<img>` or link load from a web page, which Chrome marks with `Sec-Fetch-Site: cross-site`.
- A request whose `Host` is not `127.0.0.1`, `localhost` or `[::1]` gets 403, which stops DNS rebinding. A request from another machine gets 403 even if you start the server with `--host 0.0.0.0`.
- Request bodies are capped at 64 MB, images at 40 million pixels, and only JPEG, PNG, WebP, GIF and AVIF are decoded.
- The API page at `/docs` can send `GET`s, but its other requests are refused like a web page's. Use `curl` for those.

Any extension can send an `Origin` of `chrome-extension://...`. To make the server refuse every request other than a `GET` from other extensions, set `YOMIKAE_EXTENSION_ID` to the id shown on `chrome://extensions`. `GET`s carry no `Origin`, so another extension with access to `127.0.0.1` can still read the health check, a work's names and rendered pages.

The extension refuses to fetch images on your local network or your computer for a page on the web, because it fetches with your cookies and hands the result back to the page. It refuses an image that redirects there too. It judges this from the host name, so a web name that resolves to a local address, such as `cam.attacker.example` pointing at `192.168.1.1`, is still fetched. Turning the extension on only for sites you trust limits this. [docs/contract.md](docs/contract.md#who-may-call-the-server) has the exact rules.

## Supported versions

Only the latest commit on `main` is supported.
