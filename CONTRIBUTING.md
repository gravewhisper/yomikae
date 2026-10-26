# Contributing to Yomikae

Thanks for helping. Yomikae is a small project, so the process is light.

## Before you start

- For a bug, open an issue with the bug report form first, unless the fix is a few lines.
- For a new feature or a change to how translation or lettering works, open an issue to talk it over before you write much code. [docs/decisions.md](docs/decisions.md) explains why things work as they do, and what was tried and dropped.
- To report a security problem, follow [SECURITY.md](SECURITY.md) instead of opening an issue.

## Set up

Follow the README's [Install](README.md#install) steps. [AGENTS.md](AGENTS.md) has the rest: the layout, how to run the tests, and the browser tests. It applies to people as much as to coding agents.

## Make a change

1. Fork the repo and create a branch from `main`.
2. Make the change, with tests for any behavior you add or fix.
3. Run the checks:
   - Server, from `server/`: `CUDA_VISIBLE_DEVICES= PYTHONPATH=$PWD .venv/bin/python -m pytest -q`
   - Extension, from `extension/`: `pnpm test`, `pnpm typecheck` and `pnpm build`
4. Update the docs the change touches. AGENTS.md lists which ones, such as `docs/contract.md` for the HTTP API and `PIPELINE_VERSION` for output changes.
5. If you change translation or lettering, measure it as [docs/benchmarks.md](docs/benchmarks.md) describes, and put the before and after numbers in the pull request.
6. Open a pull request against `main`.

CI runs the extension checks and a quick lint of the server on every pull request. The server tests need the model files, so run them yourself before you open the pull request.

## Rules

These come from [AGENTS.md](AGENTS.md), and pull requests that break them can't be merged:

- No code comments. Keep pragmas such as `# noqa`.
- Match the style of the code around your change.
- Write the commit message as one plain sentence in the imperative that says what changed.
- Never commit model weights, fonts other than the bundled ones, datasets, benchmark pages, reports or manga images of any kind. Tests use synthetic pages from `server/tools/make_test_page.py`.
- Site adapters are accepted only for legitimate platforms where artists publish their own work. Keep any other adapter in a private folder, as [docs/adapters.md](docs/adapters.md) explains.
- Never use sexual content involving minors or characters who look like minors in tests, screenshots, benchmarks or reports.

## License

Yomikae is licensed under GPL-3.0-only. By sending a pull request, you agree that your contribution is licensed the same way.
