# Changelog

Yomikae follows [Semantic Versioning](https://semver.org/). While the version starts with 0, a release with new features raises the middle number and a release with only fixes raises the last one. [CONTRIBUTING.md](CONTRIBUTING.md#releases) explains how to cut a release.

## 0.1.0 - 2026-10-27

The first tagged version. It is the state of the project when the repo went public.

### Translation

- A Chrome MV3 extension sends each page image to a server on your own computer, which finds the text, reads it with manga-ocr, translates it, erases the Japanese and letters the English into the balloons.
- Any OpenAI-compatible LLM can translate. The default is Gemma 4 12B in LM Studio, with up to 2 earlier pages, a list of the names the work already uses and a translation memory as context.
- LM Studio's transient errors are retried twice. A request over the model's context size fails at once with a clear error.

### Reading

- Built-in adapters for pixiv and X. Any other site works in click-to-translate mode.
- Pages after the one you are reading translate ahead of you. Hold Alt to see the original.
- The Report button saves a bad page with your marks and a note, for debugging.
- Translated pages are cached on disk.

### Measured

- On the 214 OpenMantra pages, with an RTX 5070 Ti, the translate call takes a median of about 1.4 s per page and scores COMET 0.7475 and chrF 38.47. [docs/models.md](docs/models.md) and [docs/performance.md](docs/performance.md) have every benchmark.
