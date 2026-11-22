# Credits and licenses

Yomikae is licensed under the MIT License, see [LICENSE](../LICENSE).

`docs/media/demo.gif` and `docs/media/demo.png` are not MIT. They show OpenMantra pages and are licensed CC BY-NC 4.0, like the dataset.

## Code and ideas from other projects

The LaMa inpainting network in `server/yomikae/lama.py` is adapted from [advimman/lama](https://github.com/advimman/lama), Copyright 2021 Samsung Research, under the Apache License 2.0. Its license is in [server/licenses/LaMa-Apache-2.0.txt](../server/licenses/LaMa-Apache-2.0.txt).

Several techniques come from [zyddnys/manga-image-translator](https://github.com/zyddnys/manga-image-translator): refining the text mask with a dense CRF and growing it by glyph size, retrying a misaligned translation batch in halves, keeping English at least as large as the source lettering, and two detection recall passes. Yomikae's code for them was written from a description of their behavior, and contains no code from that project. [decisions.md](decisions.md#mit-license) explains how.

The typesetting rules follow AnonBlack's Typesetting Guide. [typesetting-notes.md](typesetting-notes.md) maps each rule to the code.

## Models

The repo includes no model weights. You download them during install.

| Model | Used for | License |
| --- | --- | --- |
| [comic-text-detector](https://github.com/dmMaze/comic-text-detector), ONNX export from the manga-image-translator `beta-0.3` release | Text detection | GPL-3.0 |
| [manga-ocr](https://github.com/kha-white/manga-ocr) (`kha-white/manga-ocr-base`) | OCR | Apache-2.0 |
| [LaMa](https://github.com/advimman/lama), manga-tuned weights from [dreMaz/AnimeMangaInpainting](https://huggingface.co/dreMaz/AnimeMangaInpainting) | Inpainting | Apache-2.0 (LaMa), MIT (weights) |
| [huyvux3005/manga109-segmentation-bubble](https://huggingface.co/huyvux3005/manga109-segmentation-bubble), a YOLO11n-seg model | Speech balloon outlines | Apache-2.0 on the model card, AGPL-3.0 in the Ultralytics export metadata, non-commercial training data |

The balloon segmenter was trained on Manga109 and MS92/MangaSegmentation. Manga109 may be used only for non-commercial research, so treat these weights as fit for personal use and not for a commercial product. To use a different model, set `YOMIKAE_BUBBLE_MODEL` to another Ultralytics segmentation export in `server/models/`. The server runs the exported ONNX file with onnxruntime. It uses Ultralytics only for the one-time export.

## Fonts

| Font | Used for | License |
| --- | --- | --- |
| Shantell Sans Medium | Dialogue, when Anime Ace 2 is absent | OFL-1.1 |
| Bangers | Shouts | OFL-1.1 |
| Comic Neue Regular and Bold | Dialogue and shouts with `YOMIKAE_CASE=mixed` | OFL-1.1 |
| Noto Sans Symbols and Noto Sans Symbols 2 | Symbols such as ♡ ♪ ★ that the lettering face lacks | OFL-1.1 |
| Anime Ace 2 (Blambot) | Dialogue, if you add it | Blambot license, not included |

Each OFL font's license file is in `server/fonts/`.

## Libraries

The server depends on PyTorch (BSD-3-Clause), onnxruntime (MIT), OpenCV (Apache-2.0), NumPy (BSD-3-Clause), Pillow (MIT-CMU), FastAPI (MIT), Uvicorn (BSD-3-Clause), python-multipart (Apache-2.0), HTTPX (BSD-3-Clause), fontTools (MIT), Pyphen (used under MPL-1.1), and pydensecrf (MIT). Pyphen's `en_US` hyphenation patterns are under a BSD-style license. The extension is built with esbuild (MIT) and TypeScript (Apache-2.0) and ships no third-party runtime code. The translation benchmark uses unbabel-comet (Apache-2.0), the `Unbabel/wmt22-comet-da` model (Apache-2.0), and sacrebleu (Apache-2.0).
