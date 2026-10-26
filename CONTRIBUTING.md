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

CI runs the extension checks, a quick lint of the server and the commit message check on every pull request. The server tests need the model files, so run them yourself before you open the pull request.

## Commit messages

Commits and pull request titles follow [Conventional Commits](https://www.conventionalcommits.org/):

```
type(scope): description
```

- **type** says what kind of change it is:
  - `feat`, a new feature
  - `fix`, a bug fix
  - `perf`, faster or lighter with the same behaviour
  - `refactor`, a change to the code that keeps behaviour the same
  - `docs`, documentation only
  - `test`, tests only
  - `build`, dependencies or the build
  - `ci`, the CI workflows
  - `chore`, anything else that doesn't change the app
  - `style`, formatting only
  - `revert`, undoing an earlier commit
- **scope** is optional and names the part you changed, in lower case: `server`, `extension`, `detect`, `ocr`, `translate`, `inpaint`, `typeset`, `adapters`, `bench`, `docs`, `ci` or `deps`.
- **description** says what changed, in the imperative ("add", not "added"), with no trailing period. Keep the whole line to 100 characters.
- Add `!` after the type or scope for a change that breaks something users or adapters rely on, such as the HTTP API in `docs/contract.md`, and explain it in the body: `feat(server)!: return bubbles in reading order`.
- A body is optional. Use it to say why.

Pull requests are squash-merged, so the pull request title becomes the commit on `main`. CI checks the title of every pull request and every commit pushed to `main`. To check your commits as you write them, turn on the repo's hook once:

```sh
git config core.hooksPath .githooks
```

## Releases

Versions follow [Semantic Versioning](https://semver.org/). While the version starts with 0, a release with any `feat` raises the middle number, such as 0.1.0 to 0.2.0, and a release with only fixes raises the last one, such as 0.1.0 to 0.1.1.

To cut a release:

1. Set the new version in `server/pyproject.toml`, `server/yomikae/__init__.py` and `extension/static/manifest.json`.
2. Add a section to [CHANGELOG.md](CHANGELOG.md) built from the `feat` and `fix` commits since the last tag.
3. Commit both as `chore(release): v0.2.0` and merge it to `main`.
4. Tag that commit `v0.2.0` with `git tag -a`, push the tag, and publish a GitHub release with the changelog section as its notes.

Version tags are protected, so a tag can't be moved or deleted once it is pushed.

## Rules

These come from [AGENTS.md](AGENTS.md), and pull requests that break them can't be merged:

- No code comments. Keep pragmas such as `# noqa`.
- Match the style of the code around your change.
- Never commit model weights, fonts other than the bundled ones, datasets, benchmark pages, reports or manga images of any kind. Tests use synthetic pages from `server/tools/make_test_page.py`.
- Site adapters are accepted only for legitimate platforms where artists publish their own work. Keep any other adapter in a private folder, as [docs/adapters.md](docs/adapters.md) explains.
- Never use sexual content involving minors or characters who look like minors in tests, screenshots, benchmarks or reports.

## License

Yomikae is licensed under GPL-3.0-only. By sending a pull request, you agree that your contribution is licensed the same way.
