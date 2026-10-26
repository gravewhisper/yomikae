# AGENTS.md

Rules for AI coding agents and human contributors to Yomikae. The [README](README.md) explains what the project does and how to install it.

## Layout

- `server/` is the Python server. The pipeline is in `server/yomikae/`, the tests in `server/tests/`, and the scripts in `server/tools/`.
- `extension/` is the Chrome extension. The source is in `extension/src/`, unit tests in `extension/test/`, and browser tests in `extension/tools/`. `extension/adapters/template.ts` is a starting point for a new site adapter.
- `extension/adapters.local/` is ignored by git. The build compiles in any adapters it finds there or in the folder named by `YOMIKAE_ADAPTERS`.
- `docs/contract.md` is the contract between the extension and the server. `docs/sites.md` describes each site adapter, and `docs/adapters.md` explains how to write one.
- `docs/decisions.md` records why lettering, shouts, honorifics, translation context, site modes, fonts, and GPU use work as they do, and what was tried and dropped. Read the matching entry before you change one of them.

## Set up

Follow the README's install steps for the server venv and the three model files in `server/models/`. The server tests load every model, so they fail without those files. For the extension, run `pnpm install` in `extension/`.

In a second checkout or a git worktree, link `server/.venv` and `server/models` to the main checkout's copies. If the main checkout has `server/fonts/animeace2_reg.ttf`, link it too, or the worktree letters dialogue in a different font. Git ignores all three links.

## Test

Server tests, from `server/`:

```sh
CUDA_VISIBLE_DEVICES= PYTHONPATH=$PWD .venv/bin/python -m pytest -q
```

- `CUDA_VISIBLE_DEVICES=` hides the GPU, so the tests run on the CPU and do not compete with a running server or LLM for GPU memory. The suite has 103 tests and takes about 3 minutes on an 8-core desktop CPU.
- `PYTHONPATH=$PWD` makes Python import `yomikae` from this checkout. `pip install -e .` points the venv at the checkout it was installed from, so a shared venv in another checkout imports the wrong code without it.
- The tests build synthetic pages with `tools/make_test_page.py` and use the `fake` translator. `test_lm_studio_translates_a_page` calls a real endpoint and skips when none answers.

Extension checks, from `extension/`:

```sh
pnpm test
pnpm typecheck
pnpm build
```

`pnpm build` writes the extension to `extension/dist`. `pnpm test` also runs the tests of local adapters, and `pnpm typecheck` covers `extension/adapters.local/`.

## Run the browser tests

The scripts in `extension/tools/` drive Chromium over the DevTools protocol. They need `chromium` on `PATH`, or its path in `CHROMIUM`.

- Each script takes an output directory as its first argument and keeps a Chromium profile and screenshots there. Give every run a new directory outside the repo, such as `node tools/e2e-local.mjs "$(mktemp -d)"`. Some scripts reuse a profile they find there.
- Each script uses fixed ports, so two runs of the same script cannot share a machine. Some scripts check that their ports are free before they start, and the rest do not.
- These scripts use only local mock pages: `e2e-local.mjs`, `e2e-outage.mjs`, `e2e-slow.mjs`, and `e2e-sites.mjs`. `e2e-report.mjs` also starts the real server on port 7880 with the `fake` translator on the CPU. Build `dist` first, except for `e2e-sites.mjs`, which builds its own `dist-sites`.
- `e2e-sites.mjs` also serves the routes and runs the checks of local adapters' mock modules, as `docs/adapters.md` describes.
- The scripts with `real` in the name load live sites. Run one only when you change that site's adapter. They build `dist-sites`, which grants the sites' access at install, because a headless run cannot answer Chrome's permission prompt.

## Never commit

- Model weights, the Anime Ace font, the page cache, datasets, benchmark pages, or reports.
- Manga images of any kind, including screenshots. The exceptions are `docs/media/demo.gif` and its poster frame `docs/media/demo.png`, which show OpenMantra pages under CC BY-NC 4.0.

Tests use synthetic pages from `tools/make_test_page.py` only. `.gitignore` covers the models, the cache, the venvs, the Anime Ace files, and `extension/e2e-out/`.

## Site adapters

This repo carries only the default adapter and adapters for legitimate platforms, where artists publish their own work. Adapters for aggregator or piracy sites are not accepted. Keep any other adapter in `extension/adapters.local/` or a private repo named by `YOMIKAE_ADAPTERS`, and never commit it, its tests, its mock pages, or its docs here.

## Content rule

Never use sexual content that involves minors or characters who look like minors in tests, browser test runs and their screenshots, benchmarks, or reports. When a real-site test needs a gallery or a page, choose one that clearly meets this rule.

## Code style

- Write no code comments. Remove comments you find in code you touch. Keep pragmas such as `# noqa: E402`.
- Match the style of the surrounding code: naming, file layout, and how much each line does.
- Write commit messages as [Conventional Commits](https://www.conventionalcommits.org/): `type(scope): description`, at most 100 characters, with no trailing period, for example `fix(typeset): keep lettering inside its bubble`. [CONTRIBUTING.md](CONTRIBUTING.md#commit-messages) lists the types and scopes. CI checks every commit pushed to `main` and every pull request title.

## Keep the docs and cache in step

- When behavior that `docs/contract.md` describes changes (the HTTP API, page states, site modes, adapters, reports), update `docs/contract.md` in the same change. Update `docs/sites.md` when an adapter changes.
- When rendering or translation output changes, increase `PIPELINE_VERSION` in `server/yomikae/pipeline.py`. Pages cached under an older version are never served, and the server deletes them at startup.

## Benchmarks

`server/tools/benchmark.py` scores the full pipeline on held-out pages, and `server/tools/translation_bench.py` scores translation on OpenMantra. The README's Benchmarks section has the commands and setup. [docs/benchmarks.md](docs/benchmarks.md) says how much the scores move between runs and lists the mistakes that void a comparison.

Keep held-out pages away from development:

- While you develop, use a separate set of pages with `tools/eval_pages.py PAGES_DIR OUT_DIR`.
- Run `benchmark.py` on the held-out set only to score a finished change, and compare against a run of the base commit with the same translator.
- Do not debug from held-out renders, tune thresholds against them, or copy held-out pages into tests, fixtures, docs, or the repo.
- Keep pages and run output outside the repo.

`benchmark.py` scores bubbles by hand from `review.json`. Fill it in from the sheets in `pages/`, then rerun with `--score`. Use `--replay RUN_DIR` to hold translations fixed when you compare typesetting changes.
