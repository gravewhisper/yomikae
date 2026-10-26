# Measuring a change

Two tools in `server/tools/` measure quality, and each answers a different question:

- To check a change to detection, OCR, inpainting, or lettering, run `benchmark.py`. It runs the whole pipeline on held-out pages and counts problems a reader would notice.
- To check a change to the translation prompt or a different model, run `translation_bench.py`. It scores translation alone against professional references.

Neither dataset is in this repo. Keep pages and run output outside it.

## Measure the whole pipeline with benchmark.py

`benchmark.py` runs each held-out page through `Engine.process`, with a fresh cache, and writes one directory per run:

- `pages/` has, for each page, the render, the inpainted page before lettering, a JSON record of its blocks, and a sheet with the original beside the render. On the original, green boxes are translated blocks, orange boxes are blocks skipped as unsure, and red boxes are Japanese still detected on the render.
- `meta.json` records the commit, the path `yomikae` was imported from, the device, the translator, the dialogue font, and the peak GPU memory.
- `review.json` is a template to fill in by hand.
- `summary.json` and `scorecard.md` hold the scores.

The headline number is problems per 100 bubbles, from your review. The categories are missed text, missed small balloons, text outside its bubble, bad size, bad placement, translated non-dialogue, duplicates, and inpainting smudges. `scorecard.md` ends with the full rubric, including what counts as a bubble.

The automated metrics need no review. They count Japanese detected again on the render, English ink outside its bubble, lettering cap height against the source glyph size, and per-stage timings.

Put the pages in one folder with one subfolder per work. Each work is translated as one context, in file name order with numbers compared as numbers, so the translator sees earlier pages as it would on a site. Then, from `server/`:

```sh
.venv/bin/python tools/benchmark.py PAGES_DIR --out-root RUNS_DIR --label NAME
```

Open each sheet in `RUNS_DIR/NAME/pages/` and fill in `review.json` with the number of bubbles on the page and the count for each category. Then score the run:

```sh
.venv/bin/python tools/benchmark.py --out-root RUNS_DIR --label NAME --score
```

Other options:

- `--device cpu` hides the GPU.
- `--replay RUN_DIR` reuses the translations of an earlier run. Use it whenever you compare typesetting, see [Hold translations fixed](#hold-translations-fixed).
- `--limit N` runs the first N pages of each work, for a smoke run.

Compare a finished change against a run of the base commit with the same translator. [AGENTS.md](../AGENTS.md#benchmarks) has the rules that keep the held-out pages unseen.

## Measure translation with translation_bench.py

`translation_bench.py` runs translation alone on [OpenMantra](https://github.com/mantra-inc/open-mantra-dataset). It sends the gold Japanese of each bubble through `translate_page`, the same code the server uses, one page per call in reading order. Each series gets a fresh context store, so earlier pages, the name list, and memory recall work as on a first read.

It reports:

- COMET with `Unbabel/wmt22-comet-da`, with a 95% bootstrap interval over lines from 1000 resamples.
- chrF, case-insensitive and cased.
- Blank lines, and how many of them have a source of only punctuation.
- How many lines keep the romanized honorific, out of the lines that attach さん, ちゃん, くん, 様, 先輩, 先生, or 殿 to a name, beside the same count for the professional reference.
- Lines answered from memory without a model call.
- Median and p90 time per page for the translation call.

The run directory holds `scorecard.md`, `summary.json`, `lines.jsonl` with every line and its COMET score, `honorifics.json` with every counted line, and `samples.md` with 30 random lines for you to read.

The README's [Benchmarks](../README.md#benchmarks) section has the setup, which installs the `bench` extra and a second venv for COMET at `server/.venv-comet`. Then, from `server/`:

```sh
.venv/bin/python tools/translation_bench.py PATH/TO/open-mantra-dataset/annotation.json --out-root RUNS_DIR --label NAME
```

Other options:

- `--base-url` and `--model` pick the endpoint and model. They default to LM Studio and `google/gemma-4-12b`. To test another model, pass the identifier LM Studio shows after it loads the file.
- `--reasoning-effort` defaults to `none`. Pass an empty string to leave the field out.
- `--series NAME` runs one book, and you can repeat it. `--limit N` runs the first N pages of each series.
- `--score` scores an existing run again without calling the model.
- `--image page` sends each page's image from the annotation's `image_paths.ja` along with its lines, scaled so the long side is `--image-side` px (default 1024). `--image tagged` also draws each line's id as a small numbered tag at its text box. Each page's record keeps the prompt tokens its calls used, and the scorecard gives the median.
- A text entry in the annotation may carry `"ruby": [["漢字", "かんじ"], ...]`, the furigana printed beside it. The bench passes those readings to `translate_page` as the server passes the readings it finds on a page, so a change to how readings are used can be measured on gold text. OpenMantra has no such field; it has to be filled from the page images.
- Running a finished label again translates only the pages that failed, which helps when LM Studio drops a few requests. Delete that run's `comet.json` first, or the scores come from the old lines.

COMET runs in its own venv on the CPU, so it does not compete with the LLM for GPU memory.

A run that stops partway can continue. Run the same command with the same label, and pages already translated without an error are skipped.

## Check on a second book

OpenMantra is five little-known series. A second set catches a model that happens to suit them. On 2026-10-26 a second set was built from a book the maintainer owns, Naruto volume 1: the Japanese colour edition and Viz's English edition. The files and the set stay on the maintainer's machine and are not in this repo. To build one from a book you own:

1. Match each raw page to its English page by the artwork: downscale, compare edge maps, and pair in order. Split two-page spreads into right and left halves. Drop pages the two editions don't share.
2. Run the server's own `detect`, `balloons`, and `ocr` stages on the raw pages (on the CPU with `YOMIKAE_LOW_VRAM=1`), so models translate what Yomikae would see, OCR errors included.
3. On the matching English page, find the English balloon that overlaps the Japanese one, at an intersection over union of 0.5 or more with no rival above 0.3, and read it with tesseract. Drop lines whose English OCR fails a dictionary-word or confidence check.
4. Convert the all-caps lettering to sentence case. All caps cost every model about 0.14 COMET against the same output.
5. Write the pairs in OpenMantra's `annotation.json` format, so `translation_bench.py` runs on them unchanged.

On Naruto, 575 of 1139 lines paired. Most of the rest were narration, sound effects, and stylised shouts. Three hand checks of 30 random pairs found the balloon match right on 30 of 30, and the English usable on 27 of 28. So the set leans toward ordinary dialogue, and its references carry small OCR errors. Use it to rank models, not for absolute scores.

## Read blind, side by side

COMET and chrF compress real differences, so before you change models, have a reviewer read both outputs blind:

1. Pick about 40 random pages where the two runs differ on at least 2 lines.
2. For each page, list every line's Japanese and both translations as A and B, with the order shuffled per page. Keep the answer key in a separate file. Normalise curly quotes and apostrophes, or a model's punctuation style gives it away.
3. Have the reviewer judge each page for accuracy, natural dialogue, tone, each translation staying in its own bubble, and length, and log each error as minor or major.
4. Unblind with the key, and count page wins and major errors per model.

A reviewer that saw only the A and B labels reports counts that mean nothing until they are unblinded. On the OpenMantra read, the reviewer's raw count was A 14, B 16, which became Gemma 22, Hy-MT2 8 after unblinding. Don't let a model judge its own output.

## Compare against a frontier model

To see how much better translation is possible, have a larger model translate the same lines with the same prompt, write its output into a run directory in `translation_bench.py`'s page format, and score it with `--score`. On 2026-10-26, Claude Opus 5.5 scored COMET 0.756 against Gemma 4 12B's 0.747 to 0.749. [models.md](models.md#measured-on-our-benchmark) has the details.

## Know the noise

Both tools measure an LLM, and its output varies between runs of the same code.

**translation_bench.py.** On 2026-10-25, two runs with the development model on the same pipeline code gave COMET 0.7464 and 0.7485, and chrF 38.24 and 38.45. Only `translation_bench.py` itself differed between the two commits. Honorifics went from 27 of 27 to 25 of 27. The bootstrap interval's half-width was about 0.0075. Treat a COMET gap under about 0.01 as noise, and a difference of two or three honorific lines as noise too. On 2026-10-26, three runs of the same code gave COMET 0.7467 to 0.7493 and chrF 38.09 to 38.17.

**benchmark.py.** Between runs, totals moved by a few problems per 100 bubbles. Much of that comes from the translations. In one comparison of two runs of the same code on 2026-10-25, the translations had the same wording on only 64% of lines.

### Hold translations fixed

Translation wording changes between runs, and different wording breaks lines differently and picks different sizes. To compare typesetting, run the base commit once, then run the change with `--replay` pointed at that run:

```sh
.venv/bin/python tools/benchmark.py PAGES_DIR --out-root RUNS_DIR --label change --replay RUNS_DIR/base
```

Replay looks translations up by their Japanese source text. A line whose OCR reading changed has no match, so it still goes to the translator.

## A development set check

While you develop, check a change on pages that are not held out. Build a folder of OpenMantra pages, run `tools/eval_pages.py` at the base commit, then at the change with `--replay` pointed at the base run's `summary.json`, and compare the two summaries bubble by bubble.

On 2026-10-27 the split and centring fixes for the 2026-10-26 reports were checked this way on 46 OpenMantra pages: every fifth page of each of the 5 works, plus rasetugari 030 and 039, which held wrong splits. Both runs used Anime Ace and replayed one Gemma 4 12B run's translations, so only the 2 merged blocks got fresh translations.

- Bubbles: 289 before, 288 after. The 2 wrong splits became whole blocks.
- Size: of the 286 bubbles whose block was unchanged, 282 kept their size, 1 grew by 1 px and 3 shrank by 1 to 3 px. Those 4 moved with their page's target size, and one of them, on the page whose split was fixed, lost the shout face because the page's median glyph size moved.
- Placement: 61 bubbles moved without a size change, from laying text out at the centroid.
- Splits over all 214 OpenMantra pages, from detection alone: 66 before, 68 after. Two wrong splits are gone, and the 4 new ones separate two real bubbles or bubble text from speed lines.

On the reported pages themselves, 7 pages from one gallery, the big toned bubbles went from 7 to 15 px to 16 to 18 px. On a page from another gallery, one bubble gained the shout face the same way the OpenMantra bubble lost it: merging a stepped balloon's two blocks moved the page's median glyph size, and the bubble's ratio went from 1.71 to 1.86, past the 1.8 rule.

## Avoid these mistakes

**Use the same lettering font in every checkout.** Anime Ace 2 is ignored by git, so a new worktree does not have it, and the server silently falls back to the bundled Shantell Sans. One comparison was void because the run in a worktree set dialogue in the bundled font while the base used Anime Ace, which changed sizes on most bubbles. Link or copy `server/fonts/animeace2_reg.ttf` into each worktree, and check that `dialogue_font` in `meta.json` matches between the runs you compare. The first line of `scorecard.md` shows it too.

**Label GPU and CPU timings.** Detection takes about 0.95 s per page on the CPU and a few tens of milliseconds on the GPU. One estimate of CPU detection time was taken from a GPU run and came out 8 to 10 times too low. `meta.json` records the device, and `--device cpu` forces the CPU.

**Import the code you mean to test.** In a worktree, run the tools with `PYTHONPATH=$PWD` from `server/`, as for the tests. A shared venv otherwise imports `yomikae` from the checkout it was installed from. Both tools print the path and commit they imported at the start, and store them in `meta.json`.

**Keep held-out pages out of development.** Don't debug from held-out renders or tune thresholds against them. [AGENTS.md](../AGENTS.md#benchmarks) has the full rules.

**Load one model at a time in LM Studio.** With a second model loaded, even on the CPU, requests to the first failed with HTTP 400 "Channel Error" several times a minute. To test another model, stop the server, unload the current model, load the candidate, run the bench, then swap back.

**Strip formatting before you blame the translation.** Ministral wrapped lines in quotes and markdown, and an abliterated Gemma build answered some lines with their id tag. Count those failures, rescore with them removed, and report both numbers, so a formatting habit isn't read as poor translation, or the other way round.

**Don't trust COMET alone.** Reference metrics compress real quality gaps. The JP-TL-Bench authors found that a 350M translation model scored COMET 0.873 against GPT-4o's 0.897, while a rubric judge rated 39.5% of its translations perfect against GPT-4o's 64%. Read `samples.md` and the honorific cases before you call a translation change better or worse.
