# Design decisions

This page records why Yomikae works the way it does, and what was tried and dropped. Read the matching entry before you change one of these behaviors. [server/README.md](../server/README.md#pipeline) describes how each stage works, and [typesetting-notes.md](typesetting-notes.md) maps the lettering rules to the guide they come from.

## Lettering stays inside the bubble

**Decision.** English never runs past its bubble's outline, even when that leaves it smaller. The page gets one target size. When a bubble cannot fit its text in whole words at 75% of that target, it tries these in order and keeps the first that reaches the 75% floor:

1. Hyphenation, at most once per word, only in words of 6 letters or more, at a syllable break from pyphen's `en_US` patterns.
2. The dialogue face squeezed to 80% of its width, whole words first, then hyphenated.
3. The same at 70% of its width.

If none reaches the floor, the bubble takes the largest of those fits, which can be as small as 10 px. Text that cannot fit at 10 px shrinks inside its bubble, down to 6 px, instead of running across its neighbours.

**Why.** A clean outline with smaller lettering reads better than bigger text squeezed edge to edge or running onto the art. The owner chose this after comparing contact sheets side by side. The squeeze widths were measured too. At 75% the letters visibly distort, and 65% turns round letters into tall ovals. The cost is smaller lettering in narrow bubbles, such as those of 4-koma strips. To read one, hold **Alt** to see the original.

**Tried and rejected.** Each of these was built and reviewed on real pages:

- A page floor of 14 px. Bubbles looked crammed, and more words were hyphenated.
- The guide's stroked spill, which lets text run past the bubble edge with a white stroke. The stroke covered the bubble outlines.
- Redrawing tiny bubbles larger, with the same shape and a fresh outline. The grown bubbles looked off, and the case came up once in 214 OpenMantra pages.

## Only bursts and big lettering make a shout

**Decision.** A line is set as a shout, larger and in Bangers, only when its bubble is a spiky burst or its source lettering is clearly bigger than the page's: 1.2 times the page median with a `！`, or 1.8 times without. The translation must also be 12 words or fewer. A `!!` ending or capitals do not make a shout.

**Why.** Manga puts `！！` on ordinary, calm lines. When `!!` counted, 49 lines on the benchmark pages got the shout face, many of them in calm bubbles. The burst detector needs a drawn outline, with at least 30% ink in a 3 px ring outside the bubble, so flash and speed-line backgrounds, whose lines thin out toward the white, don't count. On the hand labels in `server/tools/openmantra_bursts.json`, 96 bursts among 1546 OpenMantra text blocks, it reaches 0.89 precision and 0.69 recall. `server/tools/real_bursts.py` reproduces the score.

**Tried and rejected.** Treating a `!!` ending as a shout, and an earlier burst rule based only on pointed lobes, which found 28% of bursts at 71% precision.

## A block is split per bubble, never per column

**Decision.** `detect.split_by_bubble` cuts a detected block only where its text sits in different bubbles or in different lobes of one bubble. A light area counts as a bubble only when it holds at least 3 times as many pixels as the text inside it. Lighter areas than that are the slivers around white-edged lettering in a toned bubble, and all of them in one block count as one area. Lobes are seeded at every level of the distance map, not only at the level with the most peaks. Each run of text, joined along the reading direction, goes to one part.

**Why.** On 2026-10-26 the owner reported big bubbles with very small, narrow text, a bubble read as three, a lobe left empty, a double bubble with all its text in one lobe, and a kana left on the page. All five came from this function, added on 2026-10-25 (5f4b46c). In a toned or gradient bubble (median gray 133 to 179 on the reported pages) only the white edge around each column is light, so every column became its own block, its own translation fragment, and its own sliver of the shared balloon, lettered at 7 to 15 px with the face squeezed to 70%. The slivers held 1.1 to 2.4 times their text's pixels. Real bubbles held 3.8 times or more on the reported pages and 5.0 times or more on all 63 correct splits over the 214 OpenMantra pages, so 3 sits in the gap. The three OpenMantra splits under 3.5 were wrong splits too. Lobe seeding at one level missed a third lobe whose neck sat lower than the other two necks (peaks 107, 90 and 56, the second merging at 72 before the third appeared at 56). Glyph pixels that crossed a lobe boundary by 3 px made two lobes look like one broken column.

Typesetting did not cause the small text. With the reported pages' detection held fixed, the typesetting of every commit back to 0b842ac (2026-10-25) letters those columns at 10 to 15 px. c5fd2c4 took one of them from 10 px to 7 px by shrinking it inside its sliver rather than letting it overflow. With the blocks whole, the same bubbles get 16 to 18 px at today's rules, with no squeeze.

**Measured.** On the 214 OpenMantra pages the split count went from 66 to 68: the 2 wrong splits are gone and 4 new ones each separate two real bubbles, or bubble text from speed lines. On a 46-page OpenMantra development set (every fifth page of each work, plus the two pages with wrong splits), with translations replayed, 2 blocks changed (both wrong splits made whole, and one of them no longer leaves a kana on the page) and no bubble lost its text. [benchmarks.md](benchmarks.md#a-development-set-check) has the size counts.

**Tried and rejected.** Nothing else was built. A rule from the median gray alone would not work: the reported toned bubbles ranged from 133 to 240, and an OpenMantra white bubble with a dark outline also split its columns.

## Text sits at the bubble's centroid

**Decision.** A block is laid out around the centroid of the bubble interior. The centre of the largest inscribed circle is a fallback for a number of lines that does not fit at the centroid.

**Why.** The layout used to try both points and keep whichever lined better. In an egg-shaped bubble the inscribed circle sits at the widest row, and the wider rows always lined better, so the owner reported text set low: 58 px below the centroid on that page. The guide asks for the block centred vertically. On the 46-page development set, 61 of 288 bubbles moved, most by a few pixels and a few by up to about a line toward the middle, and none changed size from this rule alone.

## Each lobe of a shared double bubble is its own region

**Decision.** When two or more lettered blocks share one balloon, typeset cuts the bubble into lobes before it erodes the margin. The lobes come from the splitter's seeds (`detect.lobe_marks`), flooded down the distance map one pixel level at a time, so the cut follows the neck. Each block keeps the lobes that hold most of its text, a lobe without text goes to the block whose text is nearest, and each part is eroded on its own, by 7% of its own smaller side (3 to 16 px). A block alone in its bubble, or a bubble with one lobe, is set as before.

**Why.** The whole joined shape used to be eroded with one margin from the whole bubble, then `separate()` gave each pixel to the nearer block box with a 12 px gutter. In a double bubble whose big lobe's block reaches up toward the neck, most of the small lobe went to the big lobe's block, and the small lobe's own text was left a strip beside it (about 14 px wide at its narrowest on one OpenMantra page) and lettered small. Cutting at the nearest seed instead of flooding put the cut across the big lobe, because a seed is only the small core of a lobe. OpenCV's watershed floods by colour difference, not height, and split the lobes into rings.

**Measured.** On 67 pages with translations held fixed (53 OpenMantra pages, 49 of them every page with a shared double bubble, and 14 pages of a second book), 154 of 595 bubbles share a balloon with another lettered block. 52 of them grew, 15 shrank and 87 kept their size; 60 other bubbles moved with their page's target size (44 up, 16 down). The losses come where a lobe is narrow at the top or where the old distance rule had given a block part of the neck. [benchmarks.md](benchmarks.md#a-development-set-check) has the setup.

**Tried and rejected.** Nearest-seed lobes and OpenCV's watershed, both above.

## A missed line counts by its share inside a weak block

**Decision.** `detect.uncovered_lines` keeps a rescued line group only when at least half of its box lies inside some weak block candidate (score 0.04 or more). It used to need an IoU over 0.3 with one.

**Why.** IoU punishes a small line inside a big candidate: one column inside a whole-bubble candidate scores far under 0.3 even when it lies wholly inside, while what matters is whether the detector's block head saw text where the line is. 0.5 keeps every group the old rule kept on OpenMantra; the lowest of them lies 54% inside its candidate.

**Measured.** On all 214 OpenMantra pages, 44 line groups reach this check. 6 passed before and 7 pass after; the one extra block, the tail of a sound effect, is dropped by OCR as a recovered sound outside any bubble, so no bubble is added or removed. On 243 pages of a second book no block changed. In 15 groups the two measures disagree, and in 14 of them the line score (under 0.6) or the segmentation support rejects the group anyway. All 15 are fragments of text that a block already covers, or pieces of sound effects. So on these pages the score gate, not the overlap measure, decides which lines are rescued, and the change makes no visible difference yet.

## Honorifics are kept

**Decision.** The translation keeps Japanese honorifics, romanized and joined to the name with a hyphen: -san, -chan, -kun, -sama, -senpai, -sensei, -dono, and -tan. They never become Mr., Ms., or Teacher. The system prompt in `server/yomikae/translate.py` says so.

**Why.** It is a style choice. The English keeps what the Japanese says about how characters address each other. On OpenMantra, the professional reference keeps the honorific on only 14 of the 27 lines that attach one to a name, so `translation_bench.py` reports honorifics as their own count beside the reference's.

An honorific is added only where the Japanese has one. Gemma used to add one to bare names: on OpenMantra 28 lines put a romanized honorific after a name the Japanese line gave none, 31 on Naruto volume 1 and 12 on volume 68 (on 2026-10-26, main at 211fbb9). The name list then carried the honorific to later pages, so a bare 宏也 came out as "Hiroya-kun" because an earlier line had. The prompt now says a bare name stays bare, and the name list holds names without their honorifics. With both, the counts fell to 11, 10 and 4. The honorifics kept on names that have one moved by 1 to 2 lines (26 to 25 of 27 on OpenMantra), which is within run-to-run noise.

**Family words.** A family term is translated either whole into English (big brother, mom) or whole into romanized Japanese with its honorific (Onii-sama). An English word never carries a Japanese honorific, as in "Brother-sama". The prompt says only names, honorifics and family terms said with an honorific are romanized, and that a family term is romanized whole or translated whole. `translate_lines` also strips an honorific from an English family word, because the prompt alone missed once. The old wording, "Only names and honorifics are romanized", made Gemma translate 兄 and keep さま: rasetugari's 兄さま came out as "Brother-sama" in all 4 runs without the new wording, and the name list then listed it as a name. With the お兄ちゃん example below, it had been "Onii-sama". With the new wording it was "Big brother" in 4 of 5 runs and "Brother-sama" in 1. お姉ちゃん stayed "big sister" on all 28 lines in each of those runs.

**Tried and rejected.** Naming kinship terms in the prompt. With "keep terms like お兄ちゃん as Onii-chan", Gemma 4 12B and Hy-MT2 7B both wrote Onii-chan, big brother, for お姉ちゃん, big sister: on boureisougi, Gemma did so on 7 to 14 lines per run over 3 runs. Naming お姉ちゃん as Onee-chan too stopped that, but then Gemma wrote Suzume-san for ツバメさん, her sister, in 5 of 6 runs. With no kinship example, both mistakes were gone in 3 of 3 runs, and お姉ちゃん reads as big sister.

## A whole page goes to the translator in one call

**Decision.** The translator gets every line of a page in one call. The call also carries up to 2 earlier pages of the same work, taken only from the 5 pages before this one, the names the work already uses, and the lines of this page that already have a translation. A line that matches a line already translated in the same work reuses that translation and is not sent.

**Why.** Japanese often splits one sentence over two or three bubbles, with the object before the verb. Translating the page in reading order lets the model move words between a speaker's consecutive bubbles so each reads naturally in English. Earlier pages resolve who is speaking and keep the tone. The name list keeps spellings the same from page to page. A line counts toward a spelling only when the model wrote that spelling while the list did not already give it, so copies of a listed spelling add nothing. A name misread once by OCR can still be listed first, but a spelling the model writes against it on later lines overtakes it, where before the copies kept the first spelling forever. On the benchmark books, which have no misreads, the list held the same names on every page as before. Each name goes with a Japanese line it was translated from, because a bare list of English names reads as a list of who is in the work: on boureisougi, Gemma 4 12B wrote Suzume-san, a listed name, for ツバメさん, her sister, who was not in the list, in 3 of 3 runs, and in none of 3 once each name carried its line. Reused lines make a recurring caption read the same on every page. After a jump ahead, older pages say nothing about the current one, so context stops at 5 pages back.

**Tried and rejected.**

- Listing a name only once two lines use it. It delayed the list until the model had drifted: on tencho_isoro, 店長 came out five ways (you, Manager-san, Tencho, Manager, Shop Manager-san) in each of two runs, where the one-line list held one form on 15 or 16 lines.
- Reusing very short lines. On the translation benchmark, one wrong translation of `？` was reused across a whole series. Lines with fewer than 2 letters, kana, or kanji are now always sent to the model.

## A cut-off sound before the same sound is a stutter

**Decision.** A bubble that holds only one kana, with or without っ, dots or a dash, followed by a bubble that starts with the same kana, is lettered as a stutter of the next bubble's first English word, for example "D-" before "Don't mess with me!". The rule skips a bubble ending in ！ or ？, a next bubble that repeats the sound, as a laugh like はは does, and a bare あ, い, う, え or お. It runs after translation in `translate_page`, so the model sees the same lines as before.

**Why.** Gemma read the first bubble as a word of its own: ふっ before ふざけんなっ came out as "Heh.". On OpenMantra, 5 bubble pairs fit the rule, and it changed 4 lines of the 2026-10-26 main run: 2 real stutters fixed, 1 already right and now written the same way as the rest, and 1 that follows a mistranslated next bubble. Naruto volumes 1 and 68 have no such pairs. Blank lines stayed at 32. COMET fell from 0.7472 to 0.7468 because it scores a one-letter fragment low even where the reference has one, as "Wh..." to "Wh-" shows, so the lines were judged by reading them.

**Tried and rejected.** Counting a bare vowel. あ before ありがとう became "Th-", where the reference reads "oh...".

## A bare うち is the speaker

**Decision.** The prompt says that うち or ウチ on its own is usually the speaker's "I", "me" or "my", or the speaker's home or family, and is "we", "us" or "our" only when the speaker clearly speaks for a group.

**Why.** It is a common first person for girls and in Kansai speech. On ten lines written for the test, each sent alone three times, うちな ("I tell you") became the name "Uchina" in 3 of 3 runs before and 0 of 3 after. Neither side wrote "we", "us" or "our" for a first-person うち, and on the benchmark books the 6 bare うち lines read the same before and after. The Uchiha clan, うちは, stayed "Uchiha" on 13 of 13 lines in 3 of 4 runs; one run wrote it as "Uchi-" once. The "us" and "our" mistakes that prompted this showed only on an abliterated Q6 build of Gemma.

The wording changes of 2026-10-26 (this one, stutters, family words, added honorifics and the name count) together left COMET within noise on all three books: OpenMantra 0.7472 before and 0.7474 after, Naruto volume 1 0.6704 and 0.6711, volume 68 0.7809 and 0.7820.

## The translator replies with a list of strings

**Decision.** The model replies with `{"translations": [...]}`, one string per line in the order the lines were sent. The lines it receives still carry ids. A reply with the wrong number of strings, or with JSON syntax such as `", "` inside a string, is retried in halves.

**Why.** Writing the reply is most of the translate call, and the old reply, an object with an id and a text per line, spent about half its tokens on the ids and keys. On `translation_bench.py`, the list cut the translate time from a median of 2,322 to 2,528 ms per page to 1,358 to 1,383 ms, over three runs a side, with COMET and chrF within noise. The list also made about 10% more calls, which are counted in those times. Why the model misaligns more often with it was not measured. [performance.md](performance.md#translation-speed) has the numbers.

**Tried and rejected.**

- Ordering the request so LM Studio's prompt cache serves more of it. Earlier pages were sent as past chat turns, so each request started with the whole previous one. The cache served up to 1,222 tokens, but calls got slower, because taking a long prefix from the cache on this model costs about as much as reading it again.
- More than 2 pages in flight. LM Studio serves two requests at once, but each gets about half the speed, so the page you are reading arrives later.

## Gemma 4 12B QAT stays the translator

**Decision.** Development and the recommended setup keep Gemma 4 12B QAT at Q4_K_M as the translator.

**Why.** Four challengers with strong public numbers lost to it on OpenMantra and on a second book, in scores and in blind reads. A frontier model, Claude Opus 5.5, scored only about 0.008 COMET higher with the same prompt. [models.md](models.md#measured-on-our-benchmark) has every number.

**Tried and rejected.**

- Hy-MT2-7B: twice as fast and 3 GB smaller, but nearly twice the major errors in blind reads, mostly sentences moved into the wrong bubble.
- Ministral-3-14B-Instruct-2512: wraps lines in quotes and markdown and adds content the source lacks.
- An abliterated Gemma 4 12B at Q6_K: answers many lines with their id tag, and is lower even on the lines it translates.
- gpt-oss-20b: not run, because it leaves no room for the server on a 16 GB card, always reasons first, and refuses mature content often.

**Name errors came from the harness.** Gemma's clearest weakness was keeping characters apart: in one series it swapped two sisters' names and turned お姉ちゃん into "Onii-chan". Both came from the prompt and the name list, not the model, and were fixed (see the honorifics entry).

## Translation sends text, not the page image

**Decision.** The translator gets each page's OCR'd lines and the earlier pages as text, without the page image.

**Why.** The Gemma 4 12B QAT build used in development can read images, so on 2026-10-26 it was tested with the page attached. The page went as a 1024 px image, plain or with each line's id drawn as a numbered tag at its bubble, and one prompt sentence said what the image was. On OpenMantra, two runs per variant, COMET and chrF stayed within noise. On Naruto volume 68, the tagged image scored 0.0025 COMET lower. The tagged image did fix ビル, the name Bill, which text alone wrote as "building" on 2 lines per run, and it stopped two lines from dropping a sister's name. Speaker errors did not change. In a blind read of 20 pages, text only won 12 pages and the image won 6, with major errors 14 against 16. The image made each page 27% slower on OpenMantra and 53% slower on Naruto, where pages hold fewer lines. The fixes are too few for that cost. [models.md](models.md#measured-on-our-benchmark) has the tables.

**Tried and rejected.** The plain page image, which also left お姉ちゃん as "Onee-chan" on 16 of 28 lines per run instead of "sister", and once wrote ツバメさん as the other sister's name. The tagged page image. `translation_bench.py --image` keeps both, so a future model that can see can be checked the same way.

## The server renders the page and the extension swaps the image

**Decision.** The server returns a finished page image with the English lettered in. The extension puts it in place of the original image. It draws no text over the page.

**Why.** Erasing the Japanese already produces a new image, so the server letters the English into it in the same step. The typesetter works in the page's own pixels, against the bubble outlines it just found, and the result is a plain image that shows the same at any size the site draws it. Holding **Alt** shows the original by swapping back, and the disk cache keeps finished pages, so a page read before loads at once.

## Each site has a mode

**Decision.** Each enabled site translates either every page automatically or only the images you click **Translate** on. X, pixiv, and every site without its own adapter start in click-to-translate mode. An adapter for a reader site, where nearly every large image is a page, can start in automatic mode.

**Why.** Automatic mode on a timeline would spend GPU time on photos and avatars. On a reader site it lets the next pages translate before you reach them.

## The app is public and site adapters can stay private

**Decision.** This repo carries the default adapter and adapters for platforms where artists publish their own work. Adapters for other sites live in the gitignored `extension/adapters.local/` or in a folder named by `YOMIKAE_ADAPTERS`, and the build compiles them in.

**Why.** Manifest V3 forbids remotely hosted code, so an extension cannot download adapters at run time. Every adapter has to be compiled in when you build. Reading folders outside the repo at build time lets anyone add adapters to their own copy without putting them in this repo. [adapters.md](adapters.md) explains how.

## Local install only

**Decision.** You install the extension with **Load unpacked** from your own build. It is not on the Chrome Web Store.

**Why.** The extension does nothing without the server and an LLM running on your own computer. A store build would also contain only the adapters in this repo, while a local build compiles in your own.

## GPL-3.0-only

**Decision.** Yomikae is licensed GPL-3.0-only.

**Why.** Parts of the server are ported from manga-image-translator, whose `pyproject.toml` declares GPL-3.0-only. [server/README.md](../server/README.md#credits) names each ported file.

## Anime Ace when you have it, Shantell Sans otherwise

**Decision.** Dialogue is set in Anime Ace 2 when `server/fonts/animeace2_reg.ttf` exists. Otherwise it is set in Shantell Sans Medium, which the repo includes.

**Why.** Anime Ace 2 is one of the comic lettering faces that AnonBlack's guide names, but Blambot's license makes it free for personal use only, so the repo cannot include it. Shantell Sans Medium was the closest open-licensed match among 34 Google Fonts candidates. It scored 0.79 on letter shape against Anime Ace and has the same width, which matters because width decides line breaks and sizes. Kalam Bold, the font it replaced, scored 0.67 and is 13% narrower.

## Detection stays on the GPU

**Decision.** The text detector and the balloon segmenter run on the GPU by default, with their memory pools capped at 1.5 GB each. The server hands back PyTorch's cached memory after each page's GPU work. `YOMIKAE_DETECT_DEVICE=cpu` moves detection to the CPU, and `YOMIKAE_LOW_VRAM=1` moves everything.

**Why.** On the CPU, detection costs about 0.95 s more per page. Capping the pools and releasing the cache stopped the server from growing while you read, which was the reason to consider the CPU in the first place.

**Tried and rejected.** Detection on the CPU by default. It was built and briefly made the default on 2026-10-26, then reversed for the 0.95 s per page. [performance.md](performance.md#graphics-memory) has the measurements.
