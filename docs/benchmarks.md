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
- How many lines keep the romanized honorific, out of the lines that attach さん, ちゃん, くん, 君, 様, さま, 先輩, 先生, or 殿 to a name, beside the same count for the professional reference.
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
- When the run learns furigana names, it writes them to `names.json` in the run folder, one list per series, with each entry's `kind`. A run resumed after a stop only lists what it learned after the restart.
- Running a finished label again translates only the pages that failed, which helps when LM Studio drops a few requests. `comet.json` keeps the source, output and reference of every line it scored, so lines that changed are scored again.

COMET runs in its own venv on the CPU, so it does not compete with the LLM for GPU memory. `--comet-device auto` scores on the GPU instead when the COMET venv has a CUDA build of PyTorch and the GPU has 4 GiB free, and falls back to the CPU otherwise, saying why.

### Run it faster

- `--jobs N` translates N series at once (default 3). Pages within a series still go one at a time in reading order, each with the context of the pages before it, so every page sees what a serial run shows it. LM Studio serves 4 requests at once by default, so 3 leaves one for a server you are reading with. The page files and scorecard have the same shape as a serial run. Per-page times are longer when requests share the GPU, and the scorecard says how many were in flight, so compare translation speed only at `--jobs 1`.
- `--reuse` skips translating when a finished run in `--out-root` has the same fingerprint, a hash of the commit, the translator and its endpoint, every setting that changes a prompt, the page selection, and the bytes of the annotation. It copies that run's pages, names and COMET scores into the new label, and the scorecard names the run it came from. A run from a checkout with uncommitted changes in `server/` gets no fingerprint and is never reused.
- COMET scores each distinct line once. A line whose source, output and reference match a line in another run in `--out-root`, scored with the same COMET model, takes that score, and the scorecard says how many did. Scores taken this way differ from fresh ones by at most 3e-6. `--comet-fresh` scores every line again.
- `--pages openers` translates only the first 3 non-filler pages of each series, as `filler()` judges them. For a book of several chapters in one annotation, `--chapter-starts 7,60,84` takes 3 from each chapter. `--pages 4,9` picks pages by index in every series and `--pages SERIES:4` in one. Pages left out never enter the context store, so a later page sees less context than in a full run: compare a subset run only with runs of the same pages.

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

## Compare against manga-image-translator

On 2026-10-27 Yomikae and manga-image-translator (github.com/zyddnys/manga-image-translator at commit d6de6ab) ran all 214 OpenMantra pages end to end with the same LLM, the Gemma 4 12B build used in development in LM Studio. Vision models ran on the CPU for both. Yomikae ran at df74cf8 with live-server defaults and a context key per work. manga-image-translator ran with its `custom_openai` translator, its stock prompt, the default detector, the 48px OCR, the `lama_large` inpainter and the default renderer. With `custom_openai` it keeps no earlier-page context and no glossary. Its Python version warns on import that it is unstable and points to its Rust rewrite.

It ran twice. Stock is the tool as it ships, which sends no reasoning setting, so the model reasons. The second run added only `reasoning_effort: none` to each request, which is what Yomikae sends.

Each tool's output boxes were matched to the reference lines by overlap (30% of the smaller box), and the matched translations scored with COMET and chrF as in `translation_bench.py`.

| | Yomikae | Stock | Reasoning off |
| --- | ---: | ---: | ---: |
| Reference lines translated, of 1586 | 1546 | 1244 | 1498 |
| Pages that failed, of 214 | 0 | 27 | 0 |
| COMET, blank lines scored as 0 | 0.7295 | 0.5835 | 0.6900 |
| COMET, translated lines only | 0.7483 | 0.7439 | 0.7305 |
| chrF | 38.74 | 31.06 | 36.51 |
| Names spelled as the reference, of 113 | 90 | 37 | 45 |
| OCR character error rate | 1.0% | 3.7% | 4.3% |
| Median seconds per page, CPU | 3.96 | 22.9 | 7.5 |

- On lines both tools translated, Yomikae's COMET was 0.0081 higher than stock (95% CI 0.0026 to 0.0137), under the 0.01 this page treats as noise, and 0.0187 higher than reasoning off (CI 0.0135 to 0.0239).
- The translated-only COMET rows cover different lines. On the 1244 lines both runs of manga-image-translator translated, stock scored 0.0069 higher than reasoning off (CI 0.0029 to 0.0110), also under the noise line.
- All 27 stock failures were the model spending the whole 2048-token reply budget on reasoning and returning no text. The tool does not retry by default, so the page is lost.
- Most of the stock time is reasoning: translation took 17 of 23 s per page. With reasoning off, translation took 1.4 s, as in Yomikae, and the rest of the gap is CPU inpainting (2.6 s) and rendering (1.2 s).
- Its 48px OCR drops dakuten and cuts lines short, which is most of why it does worst on boureisougi. With no glossary, it names people afresh on each page.
- It replaces every curly apostrophe and quote in the English with a Japanese corner bracket, so 49 of its 1272 stock bubbles were lettered like "It」s". Restoring them moved COMET by 0.0005.

End-to-end COMET also scores detection, OCR and grouping, and each side ran once. Lettering and cleaning were judged in a blind side-by-side read of 30 random pages from the stock run, with the two tools shuffled as A and B on each page and the key kept apart. The project owner picked Yomikae on all 30. manga-image-translator's lettering often ran past the bubble outline or off the page, mixed text sizes on one page, and sat over Japanese it had not erased; Yomikae kept its lettering inside each bubble. The read covers presentation, not wording: on lines both tools translated, the translations scored within about 0.02 COMET.

## Following pages as context

On 2026-10-28 `translation_bench.py --lookahead N` sent the Japanese of the next N story pages with each page, marked as context that is never translated. The question was whether it fixes a chapter's first pages, where a short line that stands alone (a word, a name, an exclamation) can be read two ways until the next pages settle it. Runs used the Gemma 4 12B build used in development, on OpenMantra and on volumes 1 and 68 of the second book, with OCR text for the following pages. Variant a is today's setup, c1 adds the next story page and c2 the next two. A second run of a on each set (a2), and of c1 and c2 on OpenMantra, gives the noise.

Following pages skip filler. A page is filler when it has no Japanese, when no line sits in a balloon and it has at most 4 lines or any horizontal line, when it has at most 3 lines and one is title-sized (glyphs at least 0.065 of the page height) and horizontal or outside a balloon, or when it has at most 2 lines and at most 12 kana and kanji. This catches cover, title, credit and table-of-contents pages, and it also skips short speech pages in mid-work, such as a page with one shout, which carry little context. It misses a chapter title page with legend narration and a cast and synopsis page with one block in a balloon, and it counts one page with a single laugh as filler.

"Openers" are the first 3 non-filler annotated pages of each work and chapter: 15 pages on OpenMantra and 51 on the second book. Deltas are paired per line, with a 95% bootstrap interval that resamples pages, since all lines of a page come from one call.

| | OpenMantra | Volume 1 | Volume 68 |
| --- | ---: | ---: | ---: |
| COMET a | 0.7469 | 0.6679 | 0.7817 |
| c1 - a, all lines | +0.0005 (-0.0024 to +0.0033) | +0.0007 (-0.0030 to +0.0044) | -0.0011 (-0.0045 to +0.0024) |
| c2 - a, all lines | -0.0002 (-0.0035 to +0.0032) | +0.0005 (-0.0027 to +0.0037) | -0.0023 (-0.0062 to +0.0016) |
| a2 - a, all lines | +0.0015 (-0.0013 to +0.0044) | +0.0026 (-0.0007 to +0.0058) | +0.0000 (-0.0030 to +0.0029) |
| c1 - a, openers | +0.0119 (+0.0015 to +0.0223) | -0.0096 (-0.0218 to +0.0002) | -0.0035 (-0.0089 to +0.0022) |
| c2 - a, openers | +0.0116 (-0.0033 to +0.0260) | -0.0076 (-0.0169 to +0.0002) | -0.0084 (-0.0155 to -0.0018) |
| a2 - a, openers | +0.0054 (-0.0032 to +0.0145) | +0.0063 (-0.0040 to +0.0169) | -0.0012 (-0.0061 to +0.0040) |
| chrF a / c1 / c2 | 38.21 / 38.23 / 38.22 | 26.26 / 26.22 / 26.12 | 41.51 / 41.38 / 41.16 |
| Blank lines a / c1 / c2 | 32 / 31 / 32 | 0 / 0 / 0 | 0 / 0 / 0 |
| Median prompt tokens a / c1 / c2 | 945 / 1074 / 1123 | 869 / 1034 / 1113 | 908 / 1086 / 1148 |

- Pooled over the 66 opener pages, c1 - a was +0.0007 (-0.0053 to +0.0065) and c2 - a was -0.0006, against +0.0033 for a2 - a. The second OpenMantra pair repeated the c1 gain on its openers, +0.0116 against a2, but not the c2 gain, -0.0037.
- Time per line rose 7 to 22%. The live server shared LM Studio during the runs, so treat that as a rough figure; the prompt grew by 14 to 28% at the median.
- Leaks: no Japanese leaked into the English beyond the one line that does so in a too. A line was flagged when its English matched a line of its following pages (reference or baseline English) better than its own reference by 20 chrF points. c1 had 27 such lines and c2 54, against 25 and 55 when the same check ran on a, which never saw those pages.

COMET compresses this kind of error, so every short line (at most 8 kana and kanji) where a and c1, c2 or a2 differ was judged blind: 700 items, each with its page, the page before, the two pages after and the reference. Three judge agents saw the two English lines in random order as X and Y and gave one of: X right, Y right, both fine, both wrong. The a and a2 pairs went in mixed with the rest as a control, since two runs of the same code word 40 to 55% of lines differently.

| | Fixed | Broke | Both fine | Both wrong |
| --- | ---: | ---: | ---: | ---: |
| c1, openers | 5 | 2 | 25 | 1 |
| c1, all | 31 | 26 | 221 | 35 |
| c2, openers | 3 | 7 | 23 | 2 |
| c2, all | 35 | 37 | 222 | 34 |
| a2 against a, openers | 2 | 2 | 19 | 2 |
| a2 against a, all | 25 | 22 | 193 | 34 |

For the control, "fixed" means a2 was right and a wrong. c1 fixes and breaks lines at the same rate as a second run of the same code. The changes either way are pronouns, gender, name readings and dropped words, not lines that the following pages settle. On OpenMantra, c1 kept balloon_dream's "the higher they fly, the more the sun melts them" where a wrote "If I'm going to melt into the sun...", and c2 wrote Lene as "Reine" on the first page of tojime_no_siora. The judges flagged no line that translated ahead into the following pages.

A probe with hand-written two- and three-page works, each run 3 times per setting, shows what lookahead can do when the next page really settles a word:

| First page ends with | Lookahead 0 | 1 | 2 |
| --- | ---: | ---: | ---: |
| はし… before a page about a fallen bridge: "bridge" | 0 of 3 | 3 of 3 | 3 of 3 |
| あめ！ before a page handing over a strawberry sweet: "candy" | 0 of 3 ("Rain!") | 3 of 3 | 3 of 3 |
| 先生！ before a hospital page: "doctor" | 0 of 3 | 0 of 3 | 0 of 3 |
| み… 見え… before a page of what the speaker sees, 5 works: "I can see" | 0 of 15 | 3 of 15 | 5 of 15 |

先生 stays "Sensei!", as the honorifics entry in [decisions.md](decisions.md#honorifics-are-kept) asks. For み… 見え…, the model mostly reads the cut-off 見え… as 見えない, "Can't see...", or as a question, "Can you see...?". It never wrote "visible" in 45 runs, so a reported "Vi-visible" did not reproduce. The probe is `lookahead/probe.py` in the experiment's scratch folder and is not part of the repo.

**Earlier pages that skip filler.** The same day, `--context-skip-filler` left filler pages out of the 2 earlier pages sent as context, so a work's first story page reaches back past the cover and title pages to story pages within the 5-page reach (variant s), and s1 added lookahead 1. On the second book the bench normally translates only the annotated pages, so covers, credits and the table of contents never reached the context. `--context-stage` also translates the OCR text of every page missing from the annotation, unscored, as a real read would (variant af).

| Openers, paired against the baseline | OpenMantra | Volume 1 | Volume 68 |
| --- | ---: | ---: | ---: |
| s, against a or af | +0.0027 (-0.0077 to +0.0126) | -0.0033 (-0.0098 to +0.0019) | +0.0055 (+0.0011 to +0.0101) |
| s1, against a or af | +0.0133 (+0.0039 to +0.0213) | -0.0118 (-0.0244 to -0.0003) | +0.0013 (-0.0032 to +0.0051) |
| a, against af | | -0.0001 (-0.0063 to +0.0062) | +0.0045 (-0.0014 to +0.0106) |

With af, the openers of the second book did get credit pages, chapter title pages, author notes and the cast page as earlier context, and s replaced most of them with story pages. On OpenMantra only two openers had a filler page before them, a title page with the author's name and a one-word title page; their lines read about as differently between a and s as between a and a2. On every run the work's name list after its first three story pages held no title, author, publisher or chapter-title string. It only takes names joined to an honorific, and none of those pages have one. All overall deltas for s and s1 were within ±0.003.

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

On 2026-10-27 the per-lobe erosion for shared double bubbles was checked on 67 pages: every OpenMantra page with a double bubble that two lettered blocks share (49), 4 OpenMantra control pages, and 14 such pages from a second book. Detection, OCR and inpainting ran once at the base commit and were reused for both renders, and each block's translation was the dataset's reference English matched by overlap, so only typesetting differed. Of the 154 bubbles that share a balloon, 52 grew, 15 shrank and 87 kept their size. [decisions.md](decisions.md#each-lobe-of-a-shared-double-bubble-is-its-own-region) has the rest. The missed-line rescue change was checked with detection alone on all 214 OpenMantra pages and 243 pages of the second book: one block was added, OCR dropped it, and no bubble changed.

On 2026-10-27 the centring and big-bubble size fixes were checked the same way on all 214 OpenMantra pages and every fifth page of the second book (57 pages): detection, OCR and inpainting ran once, translations were the reference English, and each bubble's rendered ink box was measured against its region for fill and for space above and below the text. [decisions.md](decisions.md#text-sits-at-the-bubbles-centroid) has the numbers.

On 2026-10-27 the joined-bubble split fixes were checked the same way on the 90 pages whose detection changed, 43 OpenMantra pages and 47 pages of the second book. Detection, OCR and inpainting ran at the base and at the change, translations were the reference English matched by overlap (a reference line covering two blocks had its words split between them), and both renders were measured against the same lobes of the segmenter's balloons.

- Lobes that held Japanese, were cleared by inpainting and got no English: 30 before, 3 after.
- Lobes sharing one block's text with another lobe: 170 before, 15 after.
- Lobes holding two or more lettered blocks: 3 before, 11 after. 1 of the 3, a duplicate block from a neighbour's padding, is gone. Of the 9 new ones, 6 are joined pairs whose two lobes the segmenter's mask does not separate, 1 is a burst holding two shouts, and 2 are wrong splits of one bubble.
- Lettered bubbles: 754 before, 853 after. Of the 645 bubbles whose block kept its box, 470 kept their size, 64 grew and 111 shrank, most by 1 or 2 px as the page target moved; 6 shrank by 5 px or more.

On 2026-10-27 the rule that keeps side-by-side text in one block was checked with the splitter alone. The detector ran once per page on the CPU with the split turned off, and both versions of the splitter ran on those same blocks, for all 214 OpenMantra pages and 282 pages of the second book. 8 blocks on 8 pages became whole and nothing else changed. 43 of the 51 labelled joined pairs stayed split. The 20 pages for the before and after sheet were then run through OCR, inpainting and typesetting with the reference English. On the 8 changed pages, the bubbles whose block kept its box kept their size on 5 pages. On the other 3 they moved with the page target: 3 bubbles grew by 1 to 3 px on one page, all 11 moved by 1 or 2 px either way on another, and one sound effect lost 1 px on the third. [decisions.md](decisions.md#text-set-side-by-side-stays-one-block) has the rest, with the wrong splits that remain and the traced shrink cost.

On 2026-10-27 the text was tried as a way to undo the four wrong splits that remain. The detector blocks from that check, found with the split turned off, were split by main's splitter, and every one of the 288 blocks it cut was read with manga-ocr on the CPU, each piece and the whole block, from crops padded as the pipeline pads them. The boundary between pieces was tagged with the tokenizer that comes with manga-ocr. Labels were the 45 joined pairs from the OpenMantra list, the four wrong splits, and the notch cuts of the second book judged by eye (65 real pairs among the 71 notch cuts, with two sound effects left out). No rule caught more than 2 of the 4 without also firing on real pairs, and two of the four can never be reached because their first piece ends where a sentence can end. Nothing changed. [decisions.md](decisions.md#text-set-side-by-side-stays-one-block) has the numbers per rule.

## Avoid these mistakes

**Use the same lettering font in every checkout.** Anime Ace 2 is ignored by git, so a new worktree does not have it, and the server silently falls back to the bundled Shantell Sans. One comparison was void because the run in a worktree set dialogue in the bundled font while the base used Anime Ace, which changed sizes on most bubbles. Link or copy `server/fonts/animeace2_reg.ttf` into each worktree, and check that `dialogue_font` in `meta.json` matches between the runs you compare. The line under the title of `scorecard.md` shows it too.

**Label GPU and CPU timings.** Detection takes about 0.95 s per page on the CPU and a few tens of milliseconds on the GPU. One estimate of CPU detection time was taken from a GPU run and came out 8 to 10 times too low. `meta.json` records the device, and `--device cpu` forces the CPU.

**Import the code you mean to test.** In a worktree, run the tools with `PYTHONPATH=$PWD` from `server/`, as for the tests. A shared venv otherwise imports `yomikae` from the checkout it was installed from. Both tools print the path and commit they imported at the start, and store them in `meta.json`.

**Keep held-out pages out of development.** Don't debug from held-out renders or tune thresholds against them. [AGENTS.md](../AGENTS.md#benchmarks) has the full rules.

**Load one model at a time in LM Studio.** With a second model loaded, even on the CPU, requests to the first failed with HTTP 400 "Channel Error" several times a minute. To test another model, stop the server, unload the current model, load the candidate, run the bench, then swap back.

**Strip formatting before you blame the translation.** Ministral wrapped lines in quotes and markdown, and an abliterated Gemma build answered some lines with their id tag. Count those failures, rescore with them removed, and report both numbers, so a formatting habit isn't read as poor translation, or the other way round.

**Don't trust COMET alone.** Reference metrics compress real quality gaps. The JP-TL-Bench authors found that a 350M translation model scored COMET 0.873 against GPT-4o's 0.897, while a rubric judge rated 39.5% of its translations perfect against GPT-4o's 64%. Read `samples.md` and the honorific cases before you call a translation change better or worse.
