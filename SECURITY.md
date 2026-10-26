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

## Supported versions

Only the latest commit on `main` is supported.
