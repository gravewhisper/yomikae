<p align="center"><img src="docs/media/icon.png" alt="Yomikae icon" width="128"></p>

# Yomikae

[![license: GPL-3.0-only](https://img.shields.io/badge/license-GPL--3.0--only-blue)](LICENSE)
![Chrome: MV3 extension](https://img.shields.io/badge/Chrome-MV3%20extension-4285F4?logo=googlechrome&logoColor=white)
![Python: 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)
![TypeScript: strict](https://img.shields.io/badge/TypeScript-strict-3178C6?logo=typescript&logoColor=white)
![translator: local LLM, OpenAI-compatible, Claude or Codex](https://img.shields.io/badge/translator-local%20LLM%20%7C%20OpenAI--compatible%20%7C%20Claude%20%7C%20Codex-6E40C9)

Yomikae translates raw Japanese manga into English while you read it in Chrome. A browser extension sends each page image to a server on your own computer. The server finds the text, reads it, translates it with a local LLM, your Claude subscription or your ChatGPT account through Codex, erases the Japanese, and letters the English into the speech balloons. The extension then shows the translated page in place of the original.

The name comes from 読み替え (yomikae), Japanese for "reading something as something else."

![A manga page changing from Japanese to English as Yomikae translates it](docs/media/demo.gif)

*Demo pages from the OpenMantra dataset (Hinami et al., AAAI 2021), CC BY-NC 4.0, translated by Yomikae.*

## Pick a translator

Yomikae does the image work on your computer and hands the Japanese text to a translator. Pick one before you install. You can switch later.

| | Local LLM | Claude | Codex |
| --- | --- | --- | --- |
| **You need** | An NVIDIA GPU, 16 GB for the default model | A Claude subscription | A ChatGPT account, free works |
| **GPU** | Required | Optional | Optional |
| **Cost** | Free | Uses your subscription's limit | Uses Codex's monthly allowance |
| **Privacy** | Nothing leaves your computer | Page text goes to Anthropic | Page text goes to OpenAI |

Page images never leave your computer with any of them. [docs/translators.md](docs/translators.md) has the details. With a smaller GPU, [docs/models.md](docs/models.md) suggests smaller models and [docs/performance.md](docs/performance.md) shows how to move work to the CPU.

## Install

Run each block as it is, in order.

### 1. Get the tools

- **Linux.** Other systems are untested.
- **[uv](https://docs.astral.sh/uv/)**, **git** and a **C++ compiler** (`build-essential` on Ubuntu and Debian, `base-devel` on Arch).
- **Node.js** 22.23.4, 24.22.1 or 26.12.1 or newer, and **[pnpm](https://pnpm.io/)**.
- **Chrome** or **Chromium** 116 or newer.
- Your translator:
  - Local LLM: [LM Studio](https://lmstudio.ai/).
  - Claude: [Claude Code](https://claude.com/claude-code). Run `claude` once and sign in.
  - Codex: [Codex](https://developers.openai.com/codex). Run `codex` once and sign in.

### 2. Install the server

Pick the block that matches your computer.

**With an NVIDIA GPU** (about 8 GB of disk):

```sh
git clone https://github.com/gravewhisper/yomikae.git
cd yomikae/server
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python torch --index-url https://download.pytorch.org/whl/cu128
uv pip install --python .venv/bin/python -e '.[gpu,test]'
```

**Without one** (about 2 GB of disk, Claude or Codex only):

```sh
git clone https://github.com/gravewhisper/yomikae.git
cd yomikae/server
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python torch --index-url https://download.pytorch.org/whl/cpu
uv pip install --python .venv/bin/python -e '.[test]'
```

Stay in `yomikae/server` for steps 3 to 5.

### 3. Download the image models

Every translator needs these. They find, read and erase the Japanese. About 300 MB:

```sh
mkdir -p models
curl -L -o models/comictextdetector.pt.onnx https://github.com/zyddnys/manga-image-translator/releases/download/beta-0.3/comictextdetector.pt.onnx
curl -L -o models/lama_large_512px.ckpt https://huggingface.co/dreMaz/AnimeMangaInpainting/resolve/main/lama_large_512px.ckpt
```

The bubble model needs a one-time conversion. This block does it in a throwaway folder and cleans up after itself:

```sh
uv venv --python 3.12 .venv-export
uv pip install --python .venv-export/bin/python --torch-backend cpu ultralytics onnx onnxslim
curl -L -o best.pt https://huggingface.co/huyvux3005/manga109-segmentation-bubble/resolve/main/best.pt
.venv-export/bin/yolo export model=best.pt format=onnx imgsz=1600 opset=17 simplify=True
mv best.onnx models/manga109-bubble-seg.onnx
rm -rf best.pt .venv-export
```

It ends with `export success`. A fourth model, for reading Japanese, downloads itself the first time the server starts.

### 4. Start the server

Pick the line for your translator and computer:

```sh
# Local LLM. Start LM Studio's server first (see below).
.venv/bin/uvicorn yomikae.app:app --host 127.0.0.1 --port 7878

# Claude, with an NVIDIA GPU
YOMIKAE_TRANSLATOR=claude .venv/bin/uvicorn yomikae.app:app --host 127.0.0.1 --port 7878

# Claude, without a GPU
YOMIKAE_TRANSLATOR=claude YOMIKAE_LOW_VRAM=1 .venv/bin/uvicorn yomikae.app:app --host 127.0.0.1 --port 7878
```

For Codex, use the Claude lines with `codex` in place of `claude`.

<details>
<summary>Local LLM: start LM Studio's server first</summary>

Install LM Studio, then download Gemma 4 12B, load it and start the server:

```sh
lms get google/gemma-4-12b
lms load google/gemma-4-12b -y
lms server start
```

For other models, see [docs/models.md](docs/models.md).

</details>

The first start takes about a minute. Leave this terminal open, and check the server from another one:

```sh
curl http://127.0.0.1:7878/v1/health
```

You should see `"ok": true`. If you see `"ok": false`, the `detail` says what is wrong. Usually LM Studio isn't running, or `claude` or `codex` isn't installed.

### 5. Add the extension to Chrome

```sh
cd ../extension
pnpm install
pnpm build
```

1. Open `chrome://extensions` and switch on **Developer mode** in the top right.
2. Click **Load unpacked** and pick the `extension/dist` folder.
3. Open a manga page and click the Yomikae icon in the toolbar.
4. Switch on **Translate** for the site, and allow access when Chrome asks.
5. Choose **Every page, automatically** or **Only images I click Translate on**.

Done. Pages turn into English as you read.

**Next time**, just start the server again (and LM Studio, if you use it). The extension stays installed.

> **A page failed?** Hover it to see why. With Claude or Codex, `claude failed` or `codex exited with` usually means you're signed out or out of usage. Run `claude` or `codex` to check, then press **Retry**. [docs/troubleshooting.md](docs/troubleshooting.md) covers the rest.

### Optional: the Anime Ace font

Dialogue is set in Shantell Sans by default. Anime Ace 2 is the usual manga lettering face, but it is free for personal use only, so the repo does not include it. To use it, get it from [Blambot](https://blambot.com/), put `animeace2_reg.ttf` in `server/fonts/`, and restart the server.

## Read with Yomikae

- Switch Yomikae on for a site and read as usual. In automatic mode, each page shows up in English in the same place on the site.
- While you read a page, the extension translates the pages after it, so they can be ready before you turn to them.
- Hold **Alt** to see the original Japanese page. Let go to go back to English.
- Hover a page to see its chip in the top right corner. The chip shows progress while the page translates, and **Retry** if it failed.
- To fix some bubbles, click **Retranslate** in the chip or press **Alt+T**. Click the bubbles to translate again, add a hint such as "Lene is a girl" or fix a name in Japanese and English, and press Enter. Only those bubbles change.
- To report a bad page, click **Report** in the chip or press **Alt+R**. Click the bubbles that are wrong or drag a box around text that was missed, type a note, and press Enter. The server saves the page and your marks to `~/.local/share/yomikae/reports/`.
- The cast sheet in Chrome's side panel lists the characters and terms of the work you are reading, with their English spellings. Open it from the popup. You can respell, delete, or add an entry, and pages that use an old spelling are redone the next time you look at them.
- If the server stops, pages show "Waiting for server…" and continue when it comes back.
- The server caches every translated page on disk, so a page you have read before loads at once.

[docs/troubleshooting.md](docs/troubleshooting.md) covers the common problems.

## Sites

Yomikae has built-in adapters for pixiv and X (x.com and twitter.com). An adapter finds the pages of a work, fetches the full-size originals, and gives the translator earlier pages as context. Both sites start in click-to-translate mode, so nothing is translated until you click **Translate** on an image.

Any other site works in click-to-translate mode. Hover an image that is at least 200 px on each side and click **Translate** in its chip. In the popup you can switch a site to translate every page automatically.

[docs/sites.md](docs/sites.md) describes each adapter and its limits. [docs/adapters.md](docs/adapters.md) explains how to write one for another site.

## How it works

The extension finds page images, fetches them with your browser session, and posts them to the local server. The server never fetches a URL itself. For each page, the server does the following:

1. comic-text-detector finds the text blocks and a mask of the lettering.
2. A YOLO11 segmentation model trained on manga finds each speech balloon's outline.
3. manga-ocr reads the Japanese in each block. Sound effects and unsure readings are dropped.
4. The translator gets the whole page in one call, with up to two earlier pages of the same work and the names already used in it as context.
5. Plain bubbles are filled with their own color. Text on screentone or art is erased with a manga-tuned LaMa model.
6. The English is set in the balloon's shape. Shouts are set larger in Bangers.

[server/README.md](server/README.md#pipeline) describes each stage in detail. [docs/contract.md](docs/contract.md) is the HTTP API and the extension's behavior.

## Security

The server listens on `127.0.0.1` and answers only the Yomikae extension and tools on your own computer. Web pages you visit cannot use it. [SECURITY.md](SECURITY.md) explains what the server and the extension guard against, and how to report a problem.

## Status

Yomikae translates Japanese into English only. It leaves sound effects drawn into the art untranslated on purpose.

These are planned but not built yet:

- A batch mode that translates a folder of images.
- Korean and Chinese.

## More docs

- [Translators](docs/translators.md) covers the local, Claude and Codex translators and their settings.
- [Picking a translation model](docs/models.md) suggests models for each amount of graphics memory and lists the ones measured so far.
- [Speed and graphics memory](docs/performance.md) shows where a page's time goes and the settings that trade speed for memory.
- [Troubleshooting](docs/troubleshooting.md) covers common problems and how to report a bad page.
- [Measuring a change](docs/benchmarks.md) explains the two benchmark tools and how far to trust their numbers.
- [Design decisions](docs/decisions.md) records why Yomikae works the way it does and what was tried and dropped.
- [Credits and licenses](docs/credits.md) lists the code, models, fonts, and libraries Yomikae uses.

## Contributing

[CONTRIBUTING.md](CONTRIBUTING.md) explains how to propose a change. [AGENTS.md](AGENTS.md) covers the layout, the tests, and the rules for contributors and AI coding agents.

## License

Yomikae is licensed under GPL-3.0-only, see [LICENSE](LICENSE). Parts of the server are ported from manga-image-translator under the same license. The demo images are CC BY-NC 4.0, like the OpenMantra dataset they come from. [docs/credits.md](docs/credits.md) has the details.

## Disclaimer

Yomikae translates images that your browser already shows you, on your own computer. You are responsible for following the terms of the sites you read and for having the right to translate what you read. This project is not affiliated with any site it has an adapter for.
