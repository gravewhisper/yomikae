# Speed and graphics memory

This page explains where a page's time goes, what holds graphics memory, and the settings that trade one for the other. Unless it says otherwise, every number here was measured on the development machine. It has an NVIDIA GeForce RTX 5070 Ti with 16 GB and a Ryzen 7 7800X3D, and LM Studio runs the Gemma 4 12B QAT build used in development on the same GPU.

## Where a page's time goes

The server returns per-stage timings with every page, in `timings_ms`. On 2026-10-25, before the reply format change in [Translation speed](#translation-speed), the first page after startup, with 7 bubbles, took these:

| Stage | Milliseconds |
| --- | ---: |
| `detect` | 22 |
| `ocr` | 277 |
| `translate` | 2,595 |
| `inpaint` | 40 |
| `typeset` | 45 |
| `encode` | 96 |
| `total` | 3,084 |

The LLM call is most of the page. Other pages took 0.8 s with 1 bubble and 4.6 s with 11, because the model writes more for more bubbles.

Reasoning matters most. A model that thinks before it answers spends seconds on text that is thrown away. `YOMIKAE_OPENAI_REASONING_EFFORT=none`, the default, stopped Gemma from thinking and cut one call from 7.6 s to 0.7 s.

Detection, balloon segmentation, OCR, and inpainting share one GPU lock. Translation runs outside it, so the next page's detection overlaps this page's LLM call. The extension sends at most 2 pages at once. A page the server has rendered before comes from the disk cache with no work at all.

## Graphics memory

By default the LLM and the server share the GPU. On the development machine, LM Studio held about 9.7 GB for the 12B model with an 8192-token context.

### The server stays at its startup size

Before 2026-10-26, the server grew while you read. It started at about 3.0 GB, held 4.0 GB a few minutes into reading, and reached 5.0 GB after a long session. PyTorch's caching allocator kept the memory of each page's peak for reuse, and the ONNX Runtime memory arenas of the text detector and the balloon segmenter grew and kept what they took.

Since the fix, the server hands PyTorch's cached memory back to the driver every time a page's work leaves the GPU lock, so nothing from one page stays cached for the next. The detector's and the segmenter's CUDA arenas grow only by what each request asks for, and each stops at 1.5 GB. Right after start, the server then held 2,636 MiB, about 2.6 GB.

### Trade speed for memory

Two settings move work to the CPU:

| Setting | Server's GPU memory | Cost |
| --- | --- | --- |
| Default | 2.6 GB idle after start | None |
| `YOMIKAE_DETECT_DEVICE=cpu` | Less, by the detector's and segmenter's share. Not measured. | About 0.95 s more per page. The detector's two passes take about 0.8 s, and the segmenter takes 0.15 s. |
| `YOMIKAE_LOW_VRAM=1` | 0 MiB | Detection as above, plus OCR at 0.6 to 0.75 s for a page with 4 bubbles, plus LaMa at about 0.9 s for each region of text on screentone or art. A page with 11 such regions took 12 s. |

Plain bubbles are filled with their own color and never reach LaMa, so `YOMIKAE_LOW_VRAM=1` costs least on pages of plain white balloons.

On the CPU, the ONNX Runtime sessions run without a memory arena. That keeps about 1.6 GB of RAM free between pages, for about 70 ms more per page.

Detection stays on the GPU by default because 0.95 s is about a third of the 3.1 s page above. [decisions.md](decisions.md#detection-stays-on-the-gpu) has the history.

## Keep the GPU from filling up

Keep 1 to 2 GB of graphics memory free. On 2026-10-25, before the fix above, the development GPU had 15.7 of 16.3 GB in use (LM Studio 9.75 GB, the server 5.3 GB), and the desktop glitched on screen. A nearly full GPU starves the desktop compositor. Check with `nvidia-smi` while you read.

LM Studio has two load options that free memory:

- **A shorter context.** The KV cache grows with the context length, and one page needs far less than 8192 tokens. On OpenMantra, the largest page, with its two context pages and its reply, came to about 2,500 characters of dialogue. The system prompt adds about 1,100 characters. A dense page of OCR text or a long strip can have more, so leave room. Set the length when you load the model, for example `lms load google/gemma-4-12b -c 4096`.
- **Unloading when idle.** `lms load` with `--ttl <seconds>` unloads the model after that many seconds without a request, so its memory is free while you aren't reading. Load it again before you read, unless your LM Studio loads models on request by itself.

[models.md](models.md#budget-the-graphics-memory) explains how to budget memory when you pick a model.

## Disk cache

Each cached page is a folder named by the SHA-256 of the image bytes, in `server/cache/v<pipeline version>/`. It holds two files: the rendered page and `result.json`. The original image is not kept. The rendered page is WebP at quality 90 when its longer side is at most 16,383 px, JPEG at quality 90 up to 65,535 px, and PNG beyond that, so almost every page is WebP.

On 2026-10-27 the server rendered 10 OpenMantra pages on the CPU with the `fake` translator. Each `rendered.webp` was 140 to 305 KB, and each `result.json` 0.9 to 2.2 KB. The render came within 0 to 5% of the size of the source page encoded the same way, because the English replaces the Japanese at about the same amount of ink. So the source page encoded as WebP at quality 90 stands in for a cached page. Over 496 pages, the 214 OpenMantra pages and two volumes of a color manga, about 780 × 1200 px each:

| Pages | Median per page | p90 per page |
| --- | ---: | ---: |
| OpenMantra, 214 | 204 KB | 266 KB |
| Color manga volume 1, 147 | 240 KB | 295 KB |
| Color manga volume 2, 135 | 227 KB | 282 KB |
| All 496 | 223 KB | 283 KB |

Sizes are in bytes, as `YOMIKAE_CACHE_MAX_BYTES` counts them, where 1 KB is 1,000 bytes. The rendered page is more than 99% of an entry. At the median, the default 2 GiB limit holds about 9,600 pages. Pages from sites that serve larger images take more, roughly in proportion to their pixel count. The development server's cache was empty when this was measured, so these are not sizes from real reading.

Other formats for the same 496 pages, as a median per page:

| Format | Median | Against the current format |
| --- | ---: | ---: |
| WebP quality 90, encoder method 1, current | 223 KB | |
| WebP quality 90, encoder method 4 | 213 KB | 4.5% smaller |
| WebP quality 80, encoder method 1 | 176 KB | 21% smaller |
| JPEG quality 90 | 276 KB | 24% larger |
| PNG, compression level 1 | 1,073 KB | 4.8 times larger |

Nothing in an entry is wasteful. Quality 80 would store about a quarter more pages in the same space, but it may blur fine screentone, which was not checked. Method 4 saves little.

## Translation speed

On 2026-10-26, `translation_bench.py` ran all 214 OpenMantra pages three times on each side, alternating, with the Gemma 4 12B build used in development and `reasoning_effort` `none`. Times are for the translate call alone.

| Reply format | Median per page | Total for 214 pages | Output tokens | COMET | chrF |
| --- | ---: | ---: | ---: | --- | --- |
| An object with an id per line, before | 2,322 to 2,528 ms | 514 to 540 s | 32,611 to 32,901 | 0.7456 to 0.7479 | 38.06 to 38.45 |
| A list of strings, now | 1,358 to 1,383 ms | 314 to 321 s | 15,898 to 16,185 | 0.7467 to 0.7493 | 38.09 to 38.17 |

The model writes about 82 tokens a second, so a reply's length sets most of the call. The old reply spent about half its tokens on `{"id": 3, "text": ` around each line. The list makes about 10% more calls, 258 to 265 per run against 236 to 237, so more replies come back with the wrong number of lines and are retried in halves. That cost is already in the times above. The scores are within the run-to-run noise in [benchmarks.md](benchmarks.md#know-the-noise). The list format leaves 3 or 4 more lines blank per run. They are all lines of only dots, such as `......`, which the model now echoes, and a blank line keeps its original pixels, so the page looks the same.

Where a call's time goes now, summed over one run of 214 pages from LM Studio's log:

| Part | Seconds |
| --- | ---: |
| Writing the reply | 192 to 200 |
| Reading the prompt | 67 to 70 |
| LM Studio saving its prompt cache | 52 |

### LM Studio's prompt cache costs about 0.3 s on most calls

LM Studio's llama.cpp engine keeps a prompt cache in RAM. When a request shares less than half of what a slot already holds, the engine first copies that slot's state into the cache. On Gemma 4 12B the copy is 500 to 950 MB and takes a median of 290 to 350 ms, and the request waits for it. With Yomikae's prompts that happens on about two calls in three. A request that shares half or more waits 0.1 ms. The request fields `cache_prompt: false` and `id_slot` did not avoid the copy, because LM Studio does not pass them on. Plain llama.cpp turns the cache off with `--cache-ram 0`. No LM Studio setting for it was found.

### Tried and rejected

- **Reusing the prompt prefix.** The system prompt, about 290 tokens, already comes from the cache on every call. Each page's earlier-page context slides by one page, so nothing after the system prompt repeats from one call to the next. A layout that repeats it was built: earlier pages sent as past chat turns, with the context growing from 2 to 4 pages before it starts over. The cache then served up to 1,222 tokens per call, but the calls got slower. On 15 pages it took 23.7 to 27.4 s, against 20.5 to 21.0 s for the plain layout, over three alternating runs. On this model, taking a long prefix from the cache costs about as much as reading it again: 122 new tokens after 1,222 cached took 447 ms, while 942 tokens with no cache took 270 to 290 ms. The longer prompts also made each cache copy bigger, up to 1.3 s.
- **Translating more pages at once.** LM Studio does serve two requests at once on this GPU. With two pages in flight, 20 pages finished in 40.9 to 43.5 s instead of 47.8 to 48.6 s, but each call took 3.5 to 3.9 s instead of 2.1 to 2.3 s, because each request got 43 to 48 tokens a second instead of 81. The extension already sends 2 pages at once and translation already overlaps the next page's detection, so that gain is already taken. More pages in flight would make the page you are looking at slower and would halve the speed of anything else using LM Studio.
- **Sending the page image.** The tagged page image at 1024 px adds about 340 prompt tokens and the vision encoder's time to every call, about 0.4 to 0.55 s. That cost is about the same on every page, so it weighs more where pages hold fewer lines. On OpenMantra, with about 7.4 lines per page, the median page went from 1.41 to 1.45 s up to 1.79 to 1.84 s, 27% slower. On Naruto volume 68, with about 3.7 lines per page, it went from 1.05 to 1.61 s, 53% slower, and the whole volume from 156 to 229 s. Graphics memory did not change, because LM Studio already holds the vision projector with the model. A 768 px image costs about 190 tokens instead of 330 and would save about 0.1 s per page, so it was not run. [models.md](models.md#measured-on-our-benchmark) has the scores.
- **Speculative decoding** was assessed on paper only. It needs a smaller draft model with the same tokenizer as Gemma 4, held on the same GPU. The GPU has about 3 GB free, and 1 to 2 GB should stay free. Writing the reply is about 60% of a call now, so even a draft that doubled writing speed would save at most about 30% per call. Whether LM Studio's engine runs a draft model together with the JSON schema the reply uses was not checked.
- **The MTP draft head** that ships beside the development model (`mtp-gemma-4-12B-it.gguf`, 254 MB, from Unsloth) was tried on 2026-10-26. Its model card claims about 60% faster writing with identical output under llama.cpp's `--spec-type draft-mtp`, which would make a page roughly 35% faster by the 60% share above. LM Studio's `--speculative-draft-mtp` refused it, because it only accepts a head bundled inside the model's GGUF, and `--speculative-draft-simple` could not load the head as a draft model. Every Gemma 4 12B download that has a head ships it as a separate file. It needs llama-server as the backend, which Yomikae can use without code changes, or a GGUF with the head bundled.
