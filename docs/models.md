# Picking a translation model

Yomikae sends each page's dialogue to an LLM through an OpenAI-compatible endpoint. The default is Google's Gemma 4 12B in LM Studio (`google/gemma-4-12b`), but any model that meets the requirements below works. This page helps you pick one for the graphics memory you have.

Most of this page condenses web research done on 2026-10-26. None of the models in the tier tables has been run on Yomikae's own benchmark yet. The one model that has is in [Measured on our benchmark](#measured-on-our-benchmark).

## What the server asks of a model

- **An OpenAI-compatible `/chat/completions` endpoint that accepts `response_format` with a JSON schema.** The server asks for strict structured output. LM Studio enforces the schema with llama.cpp's grammar-based sampling for GGUF models and with Outlines for MLX models, according to its structured output docs. The same page warns that models under 7B may still return unreliable output.
- **A chat model that follows a system prompt.** Every request carries a system message, the page's lines with ids, up to two earlier pages as context, and the work's names. Per-line engines such as NLLB, opus-mt, or Sugoi v4 cannot take this request. Each bubble would be translated alone, without the context that resolves speakers and pronouns.
- **Japanese.** Yomikae translates Japanese to English only. Korean and Chinese are planned, and the evidence for them is thin, as [Chinese and Korean](#chinese-and-korean) explains.

The server sends `temperature: 0.3` and `reasoning_effort` from `YOMIKAE_OPENAI_REASONING_EFFORT` on every request. It sends no `top_p`, `top_k`, or repetition penalty.

## Budget the graphics memory

By default the model shares one GPU with the Yomikae server and your desktop. Add up three things:

- **The model.** The tables below estimate it as the GGUF file size, plus the KV cache for 8192 tokens in f16 from each model's `config.json`, plus about 0.4 GB of compute buffers. They skip the vision projector, because Yomikae does its own OCR.
- **The server.** Measured right after start, it held 2.6 GB with the default settings. `YOMIKAE_DETECT_DEVICE=cpu` holds less, and `YOMIKAE_LOW_VRAM=1` holds none, at a cost in speed. [performance.md](performance.md#graphics-memory) has the numbers.
- **The desktop.** Keep 1 to 2 GB free. A nearly full GPU makes the desktop glitch and stutter.

Treat the estimates as a floor. The development model's estimate is 8.0 to 8.3 GB, and LM Studio held about 9.7 GB for it on the development machine with an 8192-token context.

The tiers below leave about 1.5 GB of the card for everything else. That is enough for the desktop when the server runs with `YOMIKAE_LOW_VRAM=1`. With the server's default settings, add its 2.6 GB, or pick from the tier below your card.

## Choose by graphics memory

The evidence column uses two public ja→en benchmarks. JP-TL-Bench (JPTL) scores 0 to 10 with an LLM judge. VNTL scores visual-novel lines against references, with a confidence interval of about ±0.035. Neither uses manga. Vendor numbers are marked as such.

| GPU memory | Model and quant | Estimated memory | Evidence and notes |
| --- | --- | --- | --- |
| 4 GB | Hy-MT2-1.8B, Q8_0 | 2.4 GB | A translation model from Tencent. No ja→en number. On FLORES-200 it scores just above Gemma 4 E4B (vendor). Japanese, Chinese, and Korean. |
| 4 GB | Ministral-3-3B-Instruct-2512, Q4_K_M | 3.0 GB | JPTL 7.29, the best small general model on JP-TL-Bench. Fits only if little else is on the card. |
| 6 GB | Qwen3.5-4B, Q6_K | 4.3 GB | No ja→en number. Its predecessor Qwen3-4B scored JPTL 5.34. Thinking is on by default, see [Settings that matter](#settings-that-matter). |
| 6 GB | Ministral-3-3B-Instruct-2512, Q8_0 | 4.4 GB | The 4 GB pick at a higher precision. |
| 8 GB | shisa-v2.1-qwen3-8b, Q5_K_M | 6.5 GB with an 8-bit KV cache | JPTL 9.02, the best public ja→en score under 8 GB. Japanese only. |
| 8 GB | Qwen3.5-9B, Q4_K_M | 6.6 GB | No ja→en number. Multilingual, with a small KV cache. Thinking is on by default. |
| 8 GB | Hy-MT2-7B, Q5_K_M | 6.3 GB with an 8-bit KV cache | Level with Gemma 4 31B on Tencent's grouped FLORES-200 (vendor, averaged over 33 languages). Japanese, Chinese, and Korean. Tencent's repo has Q4_K_M, Q6_K, and Q8_0. Q5_K_M is in community conversions. |
| 10 GB | Hy-MT2-7B, Q6_K | 7.6 GB | The recommended quant. Q4_K_M lowered its instruction following from 83.1 to 75.1 on IFMTBench (vendor). |
| 10 GB | Qwen3.5-9B, Q6_K | 8.3 GB | As at 8 GB. |
| 10 GB | Gemma 4 12B QAT, Q4_K_M | 8.0 to 8.3 GB, 9.7 GB measured | The development model. No public ja→en score. At the measured 9.7 GB, it needs a 12 GB card in practice. |
| 10 GB | shisa-v2-mistral-nemo-12b, Q4_K_M | 8.5 GB with an 8-bit KV cache | VNTL 0.6985, the highest VNTL score of any model of 16B or less. Japanese only. Tight at 10 GB. |
| 12 GB | Ministral-3-14B-Instruct-2512, Q4_K_M | 10.0 GB | JPTL 9.88, above Gemma 3 27B (9.66) and GPT-4o (9.65). The strongest public ja→en number under 16 GB. |
| 12 GB | shisa-v2-mistral-nemo-12b, Q5_K_M | 9.8 GB with an 8-bit KV cache | As at 10 GB, at a higher precision. |
| 16 GB | Ministral-3-14B-Instruct-2512, Q6_K | 12.8 GB | Q8_0 does not fit. |
| 16 GB | Gemma 4 12B, Q8_0 | 13.6 GB | Shows whether the quant, not the model, limits quality. |

Gemma 4 26B-A4B, Hy-MT2-30B-A3B, and the 27B models do not fit in 16 GB at Q4.

## Where quality stops improving

On JP-TL-Bench's LLM judge, quality climbs fast up to about 8B and then flattens:

| Model | JPTL ja→en |
| --- | ---: |
| Qwen3-4B | 5.34 |
| Ministral-3-3B | 7.29 |
| Qwen3-8B | 7.94 |
| shisa-v2.1-qwen3-8b | 9.02 |
| shisa-v2.1-unphi4-14b | 9.36 |
| Ministral-3-14B | 9.88 |
| Gemini 2.5 Pro | 9.94 |

General models of 1.2B and less scored under 1.5. The big step is from 3B to 8B. From a good 8B to a good 14B is still a visible step, and past 14B the curve is flat.

Reference metrics compress these gaps. On VNTL, the 7B to 14B models all sit between 0.65 and 0.70, which is mostly a statistical tie, and GPT-4o scores 0.752. Yomikae's COMET and chrF are reference metrics too, so expect them to show smaller gaps than an LLM judge would.

Quality per GB peaks around 7B to 9B at Q5 or Q6, which needs 6.5 to 8.5 GB for the model. A 14B model at 10 to 11 GB buys a real but modest gain on the judge and little on reference metrics. Past 16 GB the research found nothing worth the memory.

## The shortlist

These are the models to run through `translation_bench.py` first, in priority order. [benchmarks.md](benchmarks.md#measure-translation-with-translation_benchpy) explains the tool.

| Model | Repo and file | Quant | Why |
| --- | --- | --- | --- |
| Hy-MT2-7B | `tencent/Hy-MT2-7B-GGUF`, `HY-MT2-7B-Q6_K.gguf` | Q6_K, or Q5_K_M for 8 GB | The best candidate per GB for Japanese, Chinese, and Korean. Its model card says the GGUF depends on Tencent's STQ kernel (llama.cpp PR #22836). Whether the standard Q4 to Q8 files load in LM Studio is unverified. If they don't, try `unsloth/Hy-MT2-7B-GGUF` or `mradermacher/Hy-MT2-7B-i1-GGUF`. |
| Qwen3.5-9B | `unsloth/Qwen3.5-9B-GGUF`, `Qwen3.5-9B-Q6_K.gguf`, or `qwen/qwen3.5-9b` in LM Studio's catalog | Q6_K and Q4_K_M | A multilingual general model with an Apache license. |
| shisa-v2-mistral-nemo-12b | `mradermacher/shisa-v2-mistral-nemo-12b-GGUF`, `shisa-v2-mistral-nemo-12b.Q4_K_M.gguf` | Q4_K_M, 8-bit KV cache | The best reference-metric score under 16 GB, at the same memory as the development model. Japanese only. |
| shisa-v2.1-qwen3-8b | `mradermacher/shisa-v2.1-qwen3-8b-GGUF`, `shisa-v2.1-qwen3-8b.Q5_K_M.gguf` | Q5_K_M, 8-bit KV cache | The best judge score under 8 GB. Japanese only. Whether Qwen3-style thinking is active is unverified. |
| Ministral-3-14B-Instruct-2512 | `mistralai/Ministral-3-14B-Instruct-2512-GGUF`, `Ministral-3-14B-Instruct-2512-Q4_K_M.gguf` | Q4_K_M | Shows whether 12 GB buys anything over the development model. |
| Hy-MT2-1.8B | `tencent/Hy-MT2-1.8B-GGUF`, `Hy-MT2-1.8B-Q8_0.gguf` | Q8_0 | The small-GPU candidate. |
| Qwen3.5-4B, optional | `unsloth/Qwen3.5-4B-GGUF`, `Qwen3.5-4B-Q6_K.gguf` | Q6_K | The 6 GB general model. |
| Ministral-3-8B-Instruct-2512, optional | `mistralai/Ministral-3-8B-Instruct-2512-GGUF`, `Ministral-3-8B-Instruct-2512-Q5_K_M.gguf` | Q5_K_M, 8-bit KV cache | Fills the gap between Ministral 3B (7.29) and 14B (9.88). Not on JP-TL-Bench. |

The research ranks TranslateGemma low. Its own report found a regression on human-rated ja→en against Gemma 3 27B, caused by mistranslated names, and names are exactly what Yomikae's glossary keeps consistent.

Community GGUF builds with refusals removed, often called abliterated, exist for Hy-MT2-7B, Qwen3.5-9B and 4B, Ministral 3 8B and 14B, shisa-v2-mistral-nemo-12b, and Gemma 4 12B and E4B. shisa-v2.1-qwen3-8b has one little-used build in safetensors only. None was found for Hy-MT2-1.8B, shisa-v2.1-unphi4-14b, or TranslateGemma.

## Settings that matter

**Thinking off for Qwen3.5.** Qwen3.5 thinks before it answers unless thinking is switched off, and a page then takes many times longer. The server sends `reasoning_effort: none` by default, which stopped Gemma 4 from thinking and cut one call from 7.6 s to 0.7 s. Whether LM Studio maps that field to Qwen3.5's switch is unverified. If pages take many seconds, set `enable_thinking: false` in the model's chat template.

**Hy-MT2's sampling.** Tencent recommends temperature 0.7, top_p 0.6, top_k 20, and repetition penalty 1.05, and no system prompt. The server always sends temperature 0.3 and a system message, so running Hy-MT2 exactly as Tencent recommends needs a code change.

**An 8-bit KV cache.** Quantizing the K and V caches to q8_0 in LM Studio's load settings roughly halves their memory. Several picks above fit their tier only with it.

**JSON schema support.** If an endpoint rejects `response_format`, the server cannot use it. LM Studio supports it for GGUF and MLX models.

**Context length.** One page needs far less than 8192 tokens. [performance.md](performance.md#keep-the-gpu-from-filling-up) has the numbers and the memory a shorter context saves.

## Licenses to watch

- Hy-MT2 (7B and 1.8B), Qwen3.5, Ministral 3, shisa-v2.1-qwen3-8b, and shisa-v2-mistral-nemo-12b use Apache 2.0. Gemma 4 is Apache 2.0 according to its model card.
- The older Hunyuan-MT and HY-MT1.5 models use a Tencent license that excludes the EU, the UK, and South Korea. Hy-MT2 does not.
- TranslateGemma and MiLMMT-46 use the Gemma Terms and are gated.
- aya-expanse is CC BY-NC, and Tower-Plus-9B is CC BY-NC-SA.
- shisa-v2.1-llama3.2-3b uses the Llama 3.2 community license. shisa-v2.1-unphi4-14b is MIT.

## Chinese and Korean

The evidence for Chinese and Korean into English is thin. WMT25 had no ja→en, ko→en, or zh→en human ranking, and no public manhwa, webtoon, or manhua benchmark for local models was found.

- **Chinese.** The Tencent line (Hunyuan-MT-7B, HY-MT1.5, Hy-MT2) is the only family of small models with a human zh→en evaluation. Hunyuan-MT-7B scored 3.258 of 4, level with Gemini 2.5 Pro, on Tencent's own test set. Qwen is Chinese-native, but its zh→en at small sizes is unmeasured.
- **Korean.** No ko→en number was found for any model under 16 GB. The Korean-native small models (EXAONE-4.0-1.2B, kanana-2-3b, HyperCLOVAX-SEED) have no translation evidence and non-standard licenses.
- **Japanese-only models.** The shisa, Sugoi, and VNTL models won't help with manhwa or manhua.

OpenMantra is Japanese only, so choosing one model for all three languages needs a manhwa and manhua set with references, or an LLM-judge pass.

## Measured on our benchmark

[benchmarks.md](benchmarks.md) explains the tools, how much their numbers move between runs, and how the blind reads work. Every run below used the same translate code (commit `46f9eae`), the same prompt, and LM Studio on an RTX 5070 Ti, on 2026-10-26.

**OpenMantra** (1586 lines, 214 pages, professional English references):

| Model | Quant | Size | COMET (95% CI) | chrF | s per page | Honorifics kept |
| --- | --- | ---: | --- | ---: | ---: | --- |
| Gemma 4 12B QAT used in development, 3 runs | Q4_K_M | 7.6 GB | 0.747 to 0.749 (each about ±0.007) | 38.1 to 38.2 | 1.36 to 1.38 | 25 to 26 of 27 |
| Hy-MT2-7B | Q4_K_M | 4.6 GB | 0.743 (0.737 to 0.751) | 34.4 | 0.63 | 26 of 27 |
| Ministral-3-14B-Instruct-2512 | Q4_K_M | 9.1 GB | 0.693, 0.712 with quotes and markdown removed | 29.1, 29.8 removed | 1.35 | 24 of 27 |
| Gemma 4 12B, an abliterated build | Q6_K | 9.9 GB | 0.706, 0.711 with id tags removed | 31.7 | 1.85 | 27 of 27 |
| Claude Opus 5.5, as a ceiling (see below) | API | n/a | 0.756 (0.750 to 0.764) | 40.5 | n/a | 23 of 27 |

**Naruto volume 1** (575 lines, 147 pages, Viz's English as the reference; [benchmarks.md](benchmarks.md#check-on-a-second-book) explains how it was built):

| Model | COMET (95% CI) | chrF | s per page |
| --- | --- | ---: | ---: |
| Gemma 4 12B QAT used in development | 0.670 (0.660 to 0.680) | 26.5 | 1.13 |
| Hy-MT2-7B | 0.661 (0.651 to 0.670) | 24.5 | 0.46 |
| Ministral-3-14B-Instruct-2512 | 0.639 (0.631 to 0.647) | 21.8 | 1.01 |
| Gemma 4 12B, an abliterated build, Q6_K | 0.533 (0.521 to 0.546) | 16.7 | 1.32 |

**Naruto volume 68** (502 lines, 135 pages, built the same way, with an extra step that repairs letters tesseract misreads in this volume's lettering):

| Model | COMET (95% CI) | chrF | s per page |
| --- | --- | ---: | ---: |
| Gemma 4 12B QAT used in development | 0.782 (0.771 to 0.793) | 41.4 | 1.15 |
| Claude Opus 5.5, as a ceiling | 0.785 (0.774 to 0.795) | 43.7 | n/a |

Scores on Naruto volume 1 run lower for every model because Viz's English is a loose localisation and its text was read back by OCR. Compare models within a table, not across them.

**Blind reads.** A separate reviewer compared 40 random pages from each set side by side without knowing which model wrote which:

| Set | Gemma wins | Hy-MT2 wins | Ties | Major errors, Gemma | Major errors, Hy-MT2 |
| --- | ---: | ---: | ---: | ---: | ---: |
| OpenMantra | 22 | 8 | 10 | 16 | 28 |
| Naruto, with Viz's English as a guide | 17 | 12 | 11 | 17 | 30 |

What each model got wrong:

- **Hy-MT2-7B** most often moves a sentence that spans two bubbles into the first one and fills the second with filler such as "Yeah.", or writes a line into a bubble that holds only "!". It also gave a line to the wrong speaker and misspelled names. Its speed and size make it the best choice when only about 6 GB of graphics memory is free, at a real cost in accuracy. The test used Q4_K_M and Yomikae's prompt, not the vendor's recommended Q6_K and sampling settings.
- **Ministral-3-14B** wraps most lines in quotes or bold markdown, adds ♡♥, and rewrites lines into punchier dialogue with content the source does not have. Removing the formatting does not close the gap. Its strong JP-TL-Bench score did not carry over.
- **The abliterated Gemma 4 12B Q6_K build** often replies with a line's id tag, such as `<6>`, instead of its translation (85 OpenMantra lines, 480 of 575 Naruto lines), and scores about 0.03 COMET lower than the QAT build even on the lines both translated. This build changes both the quant and the weights, so it does not show whether Q6_K alone would help. The QAT build is trained for 4-bit, so a gain is unlikely.
- **Gemma 4 12B QAT** mostly misreads lines. Its worst errors clustered in one series, where it wrote one sister's name for the other on 5 lines and turned お姉ちゃん into "Onii-chan" on 10 to 14. Both came from the harness, not the model: the name list held bare English spellings, and the prompt's only kinship example was お兄ちゃん as Onii-chan. Pairing each listed name with its Japanese line and dropping the example took both to 0 in 3 of 3 runs, with COMET 0.7475 and chrF 38.47 on the full set. [decisions.md](decisions.md) has the details.
- **gpt-oss-20b** was not run. Its file is about 13 GB, which leaves no room for the server on a 16 GB card, it always reasons before answering, and it refuses mature content often.

**Opus 5.5 as a ceiling.** Claude Opus 5.5 subagents translated OpenMantra with the same prompt, page by page, and were scored with `translation_bench.py --score`. They read each whole series at once, so they had more context than the 2 earlier pages the server sends. Opus led by about 0.008 COMET, inside the overlap of the intervals, and by 2.4 chrF. It made none of Gemma's name and gender errors, which the fix above has since removed, kept fewer honorifics, and sometimes moved text between bubbles too. On Naruto volume 68 the gap was smaller still: 0.003 COMET and 2.3 chrF. A frontier model buys little over Gemma 4 12B on overall quality here. Its clearest advantage is keeping characters apart.

**Page images.** On 2026-10-26, the Gemma 4 12B QAT build used in development translated with and without the page image. That build has a vision projector, and LM Studio takes the image as an OpenAI `image_url` part. Three variants ran with the same prompt and context: A sends text only, as the server does. B adds the page scaled to 1024 px on its long side. C adds the same image with each line's id drawn as a numbered tag at its text. B and C each add one prompt sentence that says what the image is. OpenMantra ran twice per variant, alternating A, B, C, A, B, C. Naruto volume 68 ran once each for A and C. Times are for the translate call alone, on an RTX 5070 Ti that also served the owner's reading.

| Set | Variant | COMET (95% CI) | chrF | Median s per page | p90 s per page | Median prompt tokens | Honorifics kept |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| OpenMantra | A, text only | 0.7487 (0.742 to 0.756), 0.7491 (0.743 to 0.756) | 38.17, 38.37 | 1.41, 1.45 | 2.35, 2.29 | 809, 805 | 26 of 27, both runs |
| OpenMantra | B, page image | 0.7501 (0.743 to 0.758), 0.7509 (0.744 to 0.759) | 38.36, 38.53 | 1.78, 1.84 | 2.59, 2.74 | 1132, 1140 | 27 of 27, both runs |
| OpenMantra | C, tagged image | 0.7519 (0.745 to 0.759), 0.7502 (0.743 to 0.757) | 38.21, 38.05 | 1.79, 1.84 | 2.99, 2.99 | 1160, 1162 | 27 of 27, both runs |
| Naruto volume 68 | A, text only | 0.7809 (0.770 to 0.792) | 41.18 | 1.05 | 1.89 | 853 | 11 of 14 |
| Naruto volume 68 | C, tagged image | 0.7784 (0.768 to 0.790) | 40.90 | 1.61 | 2.37 | 1193 | 12 of 14 |

All the score gaps are inside the noise in [benchmarks.md](benchmarks.md#know-the-noise). The image costs about 340 prompt tokens per call.

The image was meant to fix errors that come from not seeing who speaks. On boureisougi and tojime_no_siora, OpenMantra's series with those errors, counted per run:

| Error | A | B | C |
| --- | --- | --- | --- |
| ビル, the name Bill, written as "building" (5 lines) | 2, 2 | 0, 0 | 0, 0 |
| すずめ or ツバメ dropped for "her" (18 lines) | 2, 2 | 0, 0 | 0, 0 |
| ツバメさん written as Suzume-san, the other sister | 0, 0 | 1, 1 | 0, 0 |
| お姉ちゃん as a brother (31 lines) | 0, 0 | 0, 0 | 0, 0 |
| お姉ちゃん left as "Onee-chan" instead of sister (28 lines) | 0, 0 | 16, 16 | 0, 0 |

A script counted these, and two lines it flagged as missing Bill were corrected by hand, because "his height" is right there. A separate reviewer read 20 random OpenMantra pages from A and C blind, with each page image open. A won 12 pages, C won 6, and 2 were ties. Part of that gap is position bias, because A sat in the first column on 12 of the 20 pages. Major errors were 14 for A and 16 for C, and errors of speaker, gender, kinship, names or the wrong bubble were 13 and 15, depending on the column. Most speaker errors were made by both variants, on the same lines. The tagged image fixes a few name errors, but it does not tell Gemma 4 12B who speaks to whom. [decisions.md](decisions.md#translation-sends-text-not-the-page-image) has the decision.

## Sources

- VNTL Leaderboard 2026: https://huggingface.co/datasets/yukobayashi500/vntl-leaderboard-2026
- VNTL original leaderboard: https://huggingface.co/datasets/lmg-anon/vntl-leaderboard
- JP-TL-Bench paper (arXiv 2601.00223, tables in Appendix F): https://arxiv.org/abs/2601.00223v1
- JP-TL-Bench repo and scores: https://github.com/shisa-ai/jp-tl-bench and https://raw.githubusercontent.com/shisa-ai/jp-tl-bench/main/SCORES.md
- JP-TL-Bench mirror on benchmarklist.com, a secondary source: https://benchmarklist.com/models/mistralai-ministral-3-14b-instruct-2512/
- Shisa V2.1 release: https://blog.shisa.ai/posts/shisa-v2.1
- TranslateGemma technical report (arXiv 2601.09012, Table 3 for human MQM): https://arxiv.org/pdf/2601.09012
- TranslateGemma coverage: https://gigazine.net/gsc_news/en/20260116-google-translategemma and https://cxotoday.com/media-coverage/translategemma-a-new-suite-of-open-translation-models/
- Gemma releases log: https://ai.google.dev/gemma/docs/releases?hl=en
- Gemma 4 12B model card: https://huggingface.co/google/gemma-4-12B-it
- Gemma 4 license: https://ai.google.dev/gemma/docs/gemma_4_license
- Hy-MT2 model card: https://huggingface.co/tencent/Hy-MT2-7B
- Hy-MT2 report (arXiv 2605.22064, Tables 2, 4, and 5): https://arxiv.org/pdf/2605.22064
- Hy-MT2 license: https://huggingface.co/tencent/Hy-MT2-7B/blob/main/LICENSE.txt
- HY-MT1.5 license, with the territory exclusion: https://huggingface.co/tencent/HY-MT1.5-7B/blob/main/License.txt
- Hunyuan-MT technical report (arXiv 2509.05209): https://arxiv.org/html/2509.05209v2
- Hunyuan-MT-7B: https://huggingface.co/tencent/Hunyuan-MT-7B
- Seed-X-PPO-7B: https://huggingface.co/ByteDance-Seed/Seed-X-PPO-7B
- Qwen3.5-9B model card, with the WMT24++ row and the thinking default: https://huggingface.co/Qwen/Qwen3.5-9B
- Qwen3.5-4B: https://huggingface.co/Qwen/Qwen3.5-4B
- LM Studio's Qwen3.5-9B catalog page: https://lmstudio.ai/models/qwen/qwen3.5-9b
- Ministral 3: https://huggingface.co/mistralai/Ministral-3-14B-Instruct-2512, https://huggingface.co/mistralai/Ministral-3-8B-Instruct-2512, and https://huggingface.co/mistralai/Ministral-3-3B-Instruct-2512-GGUF
- Sugoi 14B Ultra: https://huggingface.co/sugoitoolkit/Sugoi-14B-Ultra-HF and https://huggingface.co/sugoitoolkit/Sugoi-14B-Ultra-GGUF
- LFM2-350M-ENJP-MT: https://huggingface.co/LiquidAI/LFM2-350M-ENJP-MT
- MiLMMT-46 abstract: https://arxiv.org/abs/2602.11961
- MiLMMT-46 12B model: https://huggingface.co/xiaomi-research/MiLMMT-46-12B-v1.0
- Tower-Plus-9B, for its license: https://huggingface.co/Unbabel/Tower-Plus-9B
- aya-expanse-8b, for its license: https://huggingface.co/CohereLabs/aya-expanse-8b
- WMT25 General MT findings, for the language pairs: https://aclanthology.org/2025.wmt-1.22
- opus-mt-ja-en model card: https://huggingface.co/Helsinki-NLP/opus-mt-ja-en
- NLLB-200 paper: https://arxiv.org/pdf/2207.04672
- LM Studio structured output: https://lmstudio.ai/docs/developer/openai-compat/structured-output
- GGUF file sizes come from `https://huggingface.co/api/models/<repo>/tree/main` for each repo named above, and KV cache sizes from each model's `config.json`.
