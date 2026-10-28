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

## A joined bubble is cut at its notches, and a block owns only its own text

**Decision.** `detect.split_by_bubble` follows the rules above, with four changes:

1. Only the text pixels inside the block's own box count. The block's mask covers a padded box, and the padding can hold the edge of a neighbour's text.
2. The column reach, twice the column width, uses the width measured in each lobe, capped at the width over the whole block.
3. When the distance map shows one lobe, the bubble is cut along the line between its two deepest outline notches. Both notches must be at least 20% of the inscribed radius deep, the line must lie at least 90% inside the bubble, and each side must hold at least 15% of its area. An outline with more than 5 notches deeper than 5% of the radius is never cut, so spiky bursts and jagged boxes keep their blocks whole. The text rules then decide as before.
4. A part is kept from 5% of the block's text, down from 10%. The 10% gate for a light area's size is unchanged.

**Why.** On 2026-10-27 the owner reported a joined pair whose English all went into one lobe while the other lobe was left blank, another joined pair handled as one bubble, and one bubble with two boxes. On OpenMantra, 57 blocks held text from two reference lines. By eye, 51 of them were joined pairs and 6 were single bubbles. 44 of the 51 never reached a lobe split, because the distance map has no second peak when a small lobe hangs on the side of a big one. Stacked lobes whose columns are offset side to side read as one column about 79 px wide over the whole block, so the reach bridged the 75 px gap at the neck. A two-kana lobe held 7% to 8% of its block's text. The one bubble with two boxes came from the padding: the top row of the next bubble's text became a third part of the bubble above.

**Measured.** Detection was run at the base and at the change on all 214 OpenMantra pages and 282 pages of a second book. 90 pages changed, and 213 blocks were added and 112 removed, matching blocks with an IoU of 0.8. 43 of the 51 joined pairs are now split, and 1 of the 6 single bubbles is split wrongly. The shallower notch of the kept pairs is at least 0.22 of the inscribed radius, while the single bubbles that a 0.15 depth split wrongly sat at 0.15 to 0.17, so 0.2 sits in that gap. The jagged single bubbles had 7 notches deeper than 5% of the radius, and the pairs had 5 or fewer. Read side by side, nearly every changed spot is a joined pair now lettered per lobe. 6 lettered spots are wrong new splits: a cloud bubble cut into by hair, a tall bubble and a cloud with shallow waists on one page, a jagged bubble, a two-word caption on art, and a last kana split off its sentence. Typeset results on the changed pages are in [benchmarks.md](benchmarks.md#a-development-set-check).

**Tried and rejected.**

- Finer distance-map levels (80 instead of 40) found 3 of the 51 pairs, and they would change typesetting of every shared bubble.
- Flooding the lobe seeds down to the neck in detection, as typesetting does. It fixed a synthetic bubble but did worse on all 6 real pages where it made a difference: text near a neck crossed the flooded cut, and the straddle rule then kept the block whole.
- A notch depth of 15% of the radius. It split 5 more single bubbles, mostly clouds and boxes with two sentences, for 1 more joined pair.

## Text set side by side stays one block

**Decision.** `detect.split_by_bubble` keeps a block whole when the text of two of its parts sits side by side, lined up over at least half the shorter part's length in the reading direction, and the gap between them is under half the block's column width. It is the side-to-side twin of the existing rule for parts stacked in one column.

**Why.** The joined-bubble fix above made new wrong splits. A two-word caption on art was cut between its lines, and the 5% share rule split the last two kana of a sentence off as their own block. In both, the two parts' text was set on one column grid: 3 px apart for a column 13 px wide, and 6 px apart for a column 18 px wide. Between the texts of two real lobes there are two margins and an outline, so their gap cannot be that small.

**Measured.** The splitter was run at main and at the change on the same detector outputs for all 214 OpenMantra pages and 282 pages of a second book. 8 blocks on 8 pages changed, and each is now one block: the two listed wrong splits, 5 more wrong splits of captions or lines on art that the earlier lane had not listed, and one big sound effect over art. 43 of the 51 labelled joined pairs are still split. On all 289 splits of one light area that main makes, the closest real joined pair has its texts 0.84 of a column apart. No other block changed.

**Not fixed.** Four wrong splits of single bubbles remain: a cloud bubble with a cusp on top and hair cutting in below, a tall bubble with a shallow bulge, a cloud with a waist, and a jagged bubble with deep waists on both sides. Measured on the 72 notch cuts at main, none of these separates them from the real pairs: the shallower notch's depth (0.21 to 0.61 of the radius, pairs 0.23 to 0.64), the cut length against the inscribed diameter (0.81 to 1.42, pairs 0.55 to 1.12, including pairs from the second book labelled by eye), the notch angle, the cut length against each side's extent along it, how far each side's text sits from that side's centre, how close the text comes to the cut, and the gap between the two sides' text (1.09 and 1.13 columns for two of them, pairs from 0.84). The segmenter's balloon mask sees one lobe in two of them but also in four real pairs.

**Tried and rejected: the text.** The idea was that one sentence running across the cut means one bubble, so pieces that OCR reads as one sentence would be merged back after OCR. It does not hold. Real joined pairs, and separate bubbles of one speaker, often carry one sentence across. Each piece of all 288 splits on the 496 pages was read with manga-ocr, as was the whole block, and the boundary between pieces was tagged with the tokenizer manga-ocr already installs.

- Two of the four wrong splits break where an utterance can end. One first piece ends on a sentence-final particle and an ellipsis. The other ends on a past-tense verb that only reads as a relative clause together with the next piece. No boundary rule reaches them, so a text rule could fix 2 of the 4 at best.
- A first piece that ends on a case or adverbial particle with no punctuation catches the other two (one ends on を, as in a jagged bubble whose first piece is a list of objects). Tagged, it also fires on 3 of the 45 labelled joined pairs and on 28 of the 239 other splits. Checked by hand, the tagger misread two of those three pairs but missed two real ones, so 3 of the 45 still end on a case particle, such as a pair whose first lobe ends on 風船で and whose second goes on with カメラを. Most of the 28 are separate bubbles that carry one sentence across.
- A first piece ending on a conjunctive particle such as が or から catches none of the four, and fires on 2 labelled pairs and 19 other splits. A second piece that opens on a particle or an auxiliary catches none of the four.
- Sentence-final punctuation on the first piece: 1 of the 4, and 25 of the 65 real pairs among the notch cuts.
- The whole block read as one gives the same letters as the two pieces joined, for all 4 and for 60 of the 65 real pairs, so OCR does not stumble on a real outline. Its confidence is 0.989 to 0.999 for the 4 and 0.83 to 1.00 for real pairs.

A merge would also have needed two new things: a mark on each block saying which detector block it was cut from, and a trusted order between two lobes, which reading order gets wrong for some pairs (one small lobe that is read first came out second). The four wrong splits stay.

**Shrink cost.** The bubbles that shrank by 5 px or more after the joined-bubble fix shrink because of page-wide sizing in typeset, from real new splits. The page target is the median of the dialogue fits, so two small new lobes on one page moved it from 28 to 20. On another page the target moved from 16 to 15, which lowered the hyphenation floor from 12 to 11, so a bubble whose largest fit was 11 was no longer refit with hyphens at 15, and it was then grouped with a 10. On a third, a sound effect's glyph size against the page median fell from 1.80 to 1.74, under the 1.8 shout rule, so it was set as dialogue at 10 instead of a shout at 15. Counting the lobes of one balloon once in these page statistics removes this; see the entry on the blocks of one balloon below.

## Text sits at the bubble's centroid

**Decision.** A block is laid out around the centroid of the bubble interior. A layout whose centre is more than 5% of the bubble's height away from the centroid counts as a fault, the same kind of fault as a line ending on "the", so a size up to 10% smaller that sits at the centroid wins over it. Before giving up on the centroid, the layout tries points on the straight way from the centroid toward the centre of the largest inscribed circle, 3 px apart, while they stay within that 5%. The inscribed circle's centre is the last resort. A bubble that grows to fill its space stops growing before its text would leave the centroid.

**Why.** The layout used to try both points and keep whichever lined better. In an egg-shaped bubble the inscribed circle sits at the widest row, and the wider rows always lined better, so the owner reported text set low: 58 px below the centroid on that page. The guide asks for the block centred vertically. That first fix made the centroid the first choice, but `fit` still took the largest size that fit anywhere. In a bubble that tapers, has a tail, or was trimmed by a neighbour, the largest size often fit only at the widest row, and the block jumped all the way there. On 2026-10-27 the owner reported text that "could be vertically centered". With translations held fixed, every one of the 8 worst off-centre bubbles on the first 67 pages checked had been laid out at the inscribed circle's centre.

**Measured.** On all 214 OpenMantra pages, with detection, OCR and inpainting run once and each block's translation held to the dataset's reference English, dialogue bubbles whose text sat more than 10% of their height off centre (space above against space below, in the text's own column) went from 97 to 49 of 1379, and those past 5% from 228 to 199. 220 bubbles changed: 75 moved toward the middle by more than 2 points of that share, 14 moved away, 79 grew and 77 shrank (most by one size, 0.93 times; the largest loss 0.73 times, a hyphenated line whose page size moved). On 57 pages of a second book, those past 10% went from 11 to 8. The remaining offsets are lines that fit only at the widest row even 10% smaller, such as one word in the wide top of a bubble with a tail. One loss is a double bubble whose second block OCR dropped: it stays one region, its centroid is the neck, and its text now sits on the neck instead of in its lobe. The text's ink also sits about 3 px above the centroid in a typical bubble (median 2% of its height), because the block is centred on room for a comma or J below the last line, which capital lettering rarely uses. That is too small to see and was left alone.

**Tried and rejected.** Walking all the way to the inscribed circle's centre in 3 px steps, without the 5% limit: it let text fit larger at off-centre points, raised page sizes, and moved 77 bubbles on 67 pages against 38 for the fault alone, with 11 placed worse. The fault without any walk, on 205 of the OpenMantra pages: about as centred (48 past 10% against 47), but it shrank 101 bubbles instead of 70 and does not move a long name toward the middle. On the same pages, letting centring take a size up to 20% or 30% smaller instead of 10% left 44 and 43 past 10%, for 18 more smaller bubbles.

## A big bubble whose text swims grows past the page band

**Decision.** Dialogue still starts at one page size, and a bubble whose text covers less than 30% of it still grows up to 1.2 times that size. A bubble of two or more lines whose text still covers less than 20% of it at that point keeps growing until it covers 20%, up to 1.5 times the page size and never past its own largest fit. A single line stays where it is, however big its bubble. Coverage here is the code's estimate: the summed line widths times the size, over the bubble's usable area.

**Why.** On 2026-10-27 the owner reported a big bubble lettered small, with too much empty space. The 1.2 cap was the cause: tall OpenMantra ovals with two short lines were held at exactly 1.2 times the page size, at 13% ink cover, while they could fit text at nearly twice the page size. Lifting the cap for every bubble would make any roomy bubble jump above its neighbours, so the band stays and only a bubble that is still mostly empty at its edge may leave it, and only until it stops looking empty. A single short line ("I", "...", "Hey, you...") in a big bubble stays at the page size: 71 of the 76 emptiest bubbles on the first pages checked were such lines, and blowing them up reads as shouting.

**Measured.** On the same 214 OpenMantra pages and 57 pages of a second book, this rule changed 43 bubbles on 39 pages: 42 grew, by a median of 1.15 times, to between 1.24 and 1.54 times their page size, and their median ink cover went from 0.20 to 0.27 of the bubble; one shrank by one size through the page's size grouping. The largest jumps are two-word lines going to near 1.5 times the page size, which fill their bubbles but stand out most.

## Each lobe of a shared double bubble is its own region

**Decision.** When two or more lettered blocks share one balloon, typeset cuts the bubble into lobes before it erodes the margin. The lobes come from the splitter's seeds (`detect.lobe_marks`), flooded down the distance map one pixel level at a time, so the cut follows the neck. Each block keeps the lobes that hold most of its text, a lobe without text goes to the block whose text is nearest, and each part is eroded on its own, by 7% of its own smaller side (3 to 16 px). A block alone in its bubble, or a bubble with one lobe, is set as before.

**Why.** The whole joined shape used to be eroded with one margin from the whole bubble, then `separate()` gave each pixel to the nearer block box with a 12 px gutter. In a double bubble whose big lobe's block reaches up toward the neck, most of the small lobe went to the big lobe's block, and the small lobe's own text was left a strip beside it (about 14 px wide at its narrowest on one OpenMantra page) and lettered small. Cutting at the nearest seed instead of flooding put the cut across the big lobe, because a seed is only the small core of a lobe. OpenCV's watershed floods by colour difference, not height, and split the lobes into rings.

**Measured.** On 67 pages with translations held fixed (53 OpenMantra pages, 49 of them every page with a shared double bubble, and 14 pages of a second book), 154 of 595 bubbles share a balloon with another lettered block. 52 of them grew, 15 shrank and 87 kept their size; 60 other bubbles moved with their page's target size (44 up, 16 down). The losses come where a lobe is narrow at the top or where the old distance rule had given a block part of the neck. [benchmarks.md](benchmarks.md#a-development-set-check) has the setup.

**Tried and rejected.** Nearest-seed lobes and OpenCV's watershed, both above.

## The blocks of one balloon count once in the page size

**Decision.** Blocks that share one balloon count as one entry in the page statistics of `typeset`: once, with the largest fit among them, in the median that sets the page target, once in the median of the base sizes that sets the page size, and once, with the largest of their glyph estimates, in the two glyph medians that decide shouts.

**Why.** Since joined bubbles are cut into lobes, one balloon can give two or three small blocks, and each of them used to count as a bubble of its own. On a page with few bubbles that moved the medians. On 2026-10-28 the owner reported lettering much smaller than the page's size on three pages, the cases the "Shrink cost" paragraph above traced: a page whose target fell from 28 to 20 when two joined pairs added four small lobes, a page whose target fell from 16 to 15 so a bubble lost its hyphenated refit and dropped to 10 px, and a sound effect whose glyph ratio fell from 1.80 to 1.74, under the shout rule, so it went from 15 to 10 px. With the lobes counted once, the first page's bubbles go from 20 and 25 px to 28 px and its lobes from 16 and 22 px to 19 to 35 px, the bubble on the second goes from 10 to 15 px, and the sound effect is a shout at 15 px again. The largest fit stands for the balloon because the joined bubble as a whole holds at least that size; the mean of the lobes was tried too (below).

**Measured.** On 67 pages, 49 OpenMantra pages (the 46-page development set plus the three reported pages) and 18 pages of a second book (every tenth annotated page plus the reported ones), with detection, OCR and inpainting run once and the dataset's reference English held fixed, 471 bubbles each side: 406 kept their size, 46 grew (by 1 to 13 px, median 2) and 19 shrank (by 1 to 7 px). 13 pages changed. 9 of the 19 losses are on one page of the second book, where 7 of 12 bubbles are shouts sized from the page size and only 5 are dialogue: counting a pair once leaves 4 entries, and the median of 4 fell from 18 to 16, so its shouts lost 1 to 5 px. On another page a calm line lost the shout face it had before (19 to 15 px, now larger-looking dialogue), and on a third a shout gained it but is set 2 px smaller. [benchmarks.md](benchmarks.md#a-development-set-check) has the setup.

**Tried and rejected.**

- The mean of the lobes' fits instead of the largest: 33 grew and 23 shrank on the same pages, with every loss of the largest-fit rule plus 4 more, such as a single bubble from 18 to 14 px.
- Feeding the notch cut into typesetting's lobes (`typeset.lobe_parts` falling back to `detect.notch_marks` when the distance map shows one lobe), so the halves of a notch-cut pair each get their own lobe instead of sharing the balloon through `separate()`. On the 53 OpenMantra and reported pages it grew 17 bubbles and shrank 24. The small lobes did gain (the halves of one wrongly split tall bubble went from 8 and 10 px to 10 and 12 px), but the big lobe's text lost the room near the neck that `separate()` had let it use, such as 13 to 11 px and 12 to 10 px in two real joined pairs, and the new fits lowered two page targets. Combined with counting once it gave 36 grew and 26 shrank, against 32 and 13 for counting once alone (with the mean). Left out.

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

**Decision.** The translator gets every line of a page in one call. The call also carries up to 2 earlier pages of the same work, taken only from the 5 pages before this one, the work's name list (see the names entry below), and the lines of this page that already have a translation. A line that matches a line already translated in the same work reuses that translation and is not sent.

**Why.** Japanese often splits one sentence over two or three bubbles, with the object before the verb. Translating the page in reading order lets the model move words between a speaker's consecutive bubbles so each reads naturally in English. Earlier pages resolve who is speaking and keep the tone. The name list keeps spellings the same from page to page. A line counts toward a spelling only when the model wrote that spelling while the list did not already give it, so copies of a listed spelling add nothing. A name misread once by OCR can still be listed first, but a spelling the model writes against it on later lines overtakes it, where before the copies kept the first spelling forever. On the benchmark books, which have no misreads, the list held the same names on every page as before. Each name goes with a Japanese line it was translated from, because a bare list of English names reads as a list of who is in the work: on boureisougi, Gemma 4 12B wrote Suzume-san, a listed name, for ツバメさん, her sister, who was not in the list, in 3 of 3 runs, and in none of 3 once each name carried its line. Reused lines make a recurring caption read the same on every page. After a jump ahead, older pages say nothing about the current one, so context stops at 5 pages back.

**Tried and rejected.**

- Listing a name only once two lines use it. It delayed the list until the model had drifted: on tencho_isoro, 店長 came out five ways (you, Manager-san, Tencho, Manager, Shop Manager-san) in each of two runs, where the one-line list held one form on 15 or 16 lines.
- Reusing very short lines. On the translation benchmark, one wrong translation of `？` was reused across a whole series. Lines with fewer than 2 letters, kana, or kanji are now always sent to the model.
- Sending the Japanese of the next one or two story pages as context, never translated. On 2026-10-28 it did not change COMET on all lines or on the first 3 story pages of each work and chapter beyond the gap between two runs of the same code, and a blind read of the 700 short lines that changed found it fixed and broke lines at the same rate as a second run (31 fixed and 26 broken for the next page, 25 and 22 for the second run). It cost 14 to 28% more prompt. It does settle a lone word when the next page really decides it: on hand-written pages, あめ！ before a page handing over a sweet went from "Rain!" to "Candy!" in 3 of 3 runs. But such lines were too rare on the benchmark books to show, and み… 見え… before a page of what the speaker sees still came out "Can't see..." or "Can you see...?" in 10 of 15 runs. In the server it would also need page N+1's OCR before page N is translated. The extension sends 2 pages at once and the GPU stages of the second follow the first under one lock, about 0.3 s, so the server could hold page N's translation that long for N+1's text from the same `context_key` and go without when N+1 is not in flight. [benchmarks.md](benchmarks.md#following-pages-as-context) has the numbers.
- Leaving filler pages (covers, credits, title and contents pages) out of the earlier pages, so the first story page gets story pages as context. It moved opener COMET by -0.003 to +0.006, within noise, and the name list never picked up a title, author or publisher from those pages.

## A line of only punctuation is never translated

**Decision.** `translate_page` never sends a line that holds only punctuation and symbols, such as ？, ！？, ．．． or ー‼. Its translation is its own symbols, NFKC-normalized to ASCII (`?`, `!?`, `...`, `-!!`).

**Why.** Given a bare ？, the model fills the slot with words: part of a neighbouring line moved over, a line from the earlier pages, or a line of its own. On OpenMantra, the `?` bubble of balloon_dream page 5 came out "with our Astronomy Club." in 15 bench runs, the end of the sentence of the line before it moved into the `?`; another `?` of the same work became "You're Aikawa-san, the one Sensei told me about, right?". Over all runs kept from the bench, 71 of 778 such lines got letters. The echo check in `translate_lines` blanked the faithful answers (a reply of `?` equals its source) while letting the invented ones through. On a real page `ocr.read` already drops a block that reads as only punctuation, so it keeps its original pixels: every one of the 34 punctuation-only bubbles of OpenMantra that the detector finds read that way with manga-ocr. So the live server sent no such line on these pages; `translation_bench.py`, which passes the dataset's text, did, and so would any reading that reaches the translator with no letters. Rendered pages do not change.

## A cut-off sound before the same sound is a stutter

**Decision.** A bubble that holds only one kana, with or without っ, dots or a dash, followed by a bubble that starts with the same kana, is lettered as a stutter of the next bubble's first English word, for example "D-" before "Don't mess with me!". The rule skips a bubble ending in ！ or ？, a next bubble that repeats the sound, as a laugh like はは does, and a bare あ, い, う, え or お. When the next bubble starts with kanji, its reading is used instead, taken from fugashi with the unidic-lite dictionary, so み before 見え… counts as a stutter. It runs after translation in `translate_page`, so the model sees the same lines as before.

**Why.** Gemma read the first bubble as a word of its own: ふっ before ふざけんなっ came out as "Heh.". On OpenMantra, 5 bubble pairs fit the rule, and it changed 4 lines of the 2026-10-26 main run: 2 real stutters fixed, 1 already right and now written the same way as the rest, and 1 that follows a mistranslated next bubble. Naruto volumes 1 and 68 have no such pairs. Blank lines stayed at 32. COMET fell from 0.7472 to 0.7468 because it scores a one-letter fragment low even where the reference has one, as "Wh..." to "Wh-" shows, so the lines were judged by reading them.

Reading the kanji added 2 OpenMantra pairs, whose cut-off sounds now come out as "Wh-" and "N-". The run with it, `w6-stut-om`, scored COMET 0.7480 with 31 blank lines.

**Tried and rejected.** Counting a bare vowel. あ before ありがとう became "Th-", where the reference reads "oh...".

A prompt line telling the model that a lone cut-off sound before a line starting with that sound is usually a stutter, to be written as "D-". On 2026-10-28, OpenMantra scored COMET 0.7488 with it (`w6-stutline3-om`) against 0.7480 without (`w6-stut-om`), within noise, with 32 blank lines against 31. Of the 133 short kana fragments before another line, 28 read differently. The real stutters were already fixed by the rule, so the prompt only added hyphen fragments where the reference has none: あ before ありがとう became "A-" again, a cut-off ラん became "Ra-", and a pained ててっ became "T-Tee!". It also split a called-out name in two, "Ni-" and "-sama...". The rule alone stays.

## A bare うち is the speaker

**Decision.** The prompt says that うち or ウチ on its own is usually the speaker's "I", "me" or "my", or the speaker's home or family, and is "we", "us" or "our" only when the speaker clearly speaks for a group.

**Why.** It is a common first person for girls and in Kansai speech. On ten lines written for the test, each sent alone three times, うちな ("I tell you") became the name "Uchina" in 3 of 3 runs before and 0 of 3 after. Neither side wrote "we", "us" or "our" for a first-person うち, and on the benchmark books the 6 bare うち lines read the same before and after. The Uchiha clan, うちは, stayed "Uchiha" on 13 of 13 lines in 3 of 4 runs; one run wrote it as "Uchi-" once. The "us" and "our" mistakes that prompted this showed only on an abliterated Q6 build of Gemma.

The wording changes of 2026-10-26 (this one, stutters, family words, added honorifics and the name count) together left COMET within noise on all three books: OpenMantra 0.7472 before and 0.7474 after, Naruto volume 1 0.6704 and 0.6711, volume 68 0.7809 and 0.7820.

## Names come from a per-work list and printed furigana

**Decision.** Each work keeps a name list. A name has its Japanese spelling, its reading in kana, its English spelling, and its source: `auto`, `furigana`, or `user`. `auto` names are the honorific names the work's earlier translations already use, built as before and never stored. `furigana` names come from the small kana printed beside a kanji name. `user` names are typed by the reader through `PUT /v1/names` and always win. The translator gets the list as the `names` map it got before. The server also reads the furigana on each page and sends the page's readings as a `readings` map, with one prompt sentence that only appears when the map is not empty. When the English of a line spells a reading of two or more kanji, that spelling is stored as a `furigana` name; a reading of one kanji is stored only with the honorific evidence described below. Each entry also has a kind: `name` for a character, `term` for a technique, place or thing. Terms go to the translator in their own `terms` map, with one prompt sentence that only appears when the map is not empty, so a work without terms sends the same prompt as before. [contract.md](contract.md#names-of-a-work) has the API.

**Why.** Gemma reads unfamiliar kanji names by guessing. On OpenMantra's rasetugari, without readings it wrote 在藤 (Arifuji) as Zaito on 15 lines, 和音 (Kazune) as Waon, and 華鏡丸 (Kakyomaru) as Kagami-maru, Hanakagami-maru and Kagamaru-maru. Manga print the reading of such names as furigana, and with the readings sent every one of those lines used the reference's name. A reader can still fix a name the furigana does not cover, and a name they fix is never overwritten.

How furigana is found: in a vertical block, the columns of large glyphs are the text, and small glyphs just right of a column are its furigana. Each run of furigana is read with manga-ocr on its own, upscaled, and so is the slice of the text column beside it, which gives the kanji it belongs to. A reading is dropped when it is not all kana, when manga-ocr is less than 0.7 sure of it, or when it is shorter than its kanji or more than 4 kana per kanji. Small つ, ゃ, ゅ and ょ are fixed where Japanese cannot have them. In a horizontal block the same search runs turned on its side: rows of large glyphs are the text and small glyphs just above a row are its furigana. Small glyphs are grouped by the 90th percentile of their size rather than the median, so a kana broken into strokes stays one group; the slice read under a group is at least one text glyph long; and a reading is dropped when a furigana piece right next to it in the same column or row could not be read, so half of a spread-out reading is never sent. These three rules apply to vertical text too. Furigana below horizontal text is not looked for. manga-ocr already leaves furigana out of the line it reads: across 2,522 readings found on all pages of Naruto volumes 1 and 68 and OpenMantra, no source line held its furigana, so the line itself is not changed.

**Measured.** On 2026-10-27 with the Gemma 4 12B build used in development, `translation_bench.py` on the dataset text, with each line's readings taken from the page images:

| Set | Run | COMET | chrF | Names kept / misspelt / missing | Median prompt tokens |
| --- | --- | ---: | ---: | --- | ---: |
| OpenMantra | main | 0.7472 | 37.98 | 59 / 11 / 24 | 801 |
| OpenMantra | readings, run 1 | 0.7494 | 38.69 | 83 / 9 / 2 | 834 |
| OpenMantra | readings, run 2 | 0.7502 | 39.21 | 82 / 5 / 7 | 838 |
| Naruto volume 1 | main | 0.6704 | 26.24 | 102 / 0 / 12 | 775 |
| Naruto volume 1 | readings | 0.6665 | 25.74 | 102 / 0 / 12 | 955 |
| Naruto volume 68 | main | 0.7809 | 41.20 | 118 / 4 / 7 | 852 |
| Naruto volume 68 | readings, run 1 | 0.7781 | 40.53 | 122 / 2 / 5 | 983 |
| Naruto volume 68 | readings, run 2 | 0.7786 | 40.44 | 115 / 7 / 7 | 962 |
| OpenMantra | horizontal, one kanji, kinds, run 1 | 0.7487 | 38.82 | 82 / 9 / 3 | 852 |
| OpenMantra | horizontal, one kanji, kinds, run 2 | 0.7481 | 38.64 | 74 / 13 / 7 | 836 |
| Naruto volume 1 | horizontal, one kanji, kinds | 0.6665 | 25.84 | 101 / 0 / 13 | 929 |
| Naruto volume 68 | horizontal, one kanji, kinds | 0.7752 | 40.67 | 122 / 2 / 5 | 1046 |
| Naruto volume 68 | same, before the one-kanji fix | 0.7735 | 39.90 | 121 / 2 / 6 | 1057 |
| Naruto volume 68 | same, every entry a name | 0.7768 | 40.25 | 100 / 9 / 20 | 1018 |
| Naruto volume 68 | same, every entry a term | 0.7739 | 40.20 | 115 / 7 / 7 | 1064 |

COMET moved within noise on all three sets. The rows for horizontal furigana, one-kanji names and kinds use readings from a fresh scan with the new grouping rules, so they differ from the earlier rows in their vertical readings too. Their COMET is also within the 0.01 noise band, but on Naruto volume 68 all four such runs sit 0.002 to 0.005 below the two earlier ones and the median prompt grew by about 90 tokens, from more readings and the `terms` map. On OpenMantra two of the three new-scan runs wrote 華鏡丸 as Kakyoumaru on its first page, and that spelling was then kept for the series: the reference's Kakyomaru is the same reading without the long vowel, and those 7 lines are most of the "misspelt" change. The names moved only on OpenMantra, where almost all the gain is rasetugari, the one series that prints furigana on its names. Naruto names are mostly in katakana, which carries no furigana, and its few kanji names were already right.

The name counts use a hand-made list of names from each reference. "Kept" is a line whose reference uses a name and whose output has it; "misspelt" has a near spelling instead; "missing" has neither. Checked by hand, 37 of 48 readings were right (25 of 32 on Naruto volume 68, 12 of 16 on OpenMantra); every reading of a name among them was right, and the mistakes were in ordinary words, mostly a lost dakuten (ご read as こ) or a small kana read wrong. Reading furigana adds about 0.5 s per page on a page where every kanji carries furigana, with manga-ocr on the GPU, and nothing on a page without it.

**Horizontal furigana.** Real coverage is thin. All pages of OpenMantra and Naruto volumes 1 and 68 hold 137 horizontal blocks, and about 27 carry furigana: Naruto volume 1's character captions, info boxes and author's notes, and the stylised chapter titles of volume 68. Readings were found in 10 blocks, all in volume 1, 39 readings in all; by hand 31 of the 39 were right, with the same kinds of mistakes as vertical text (a lost dakuten, a small ょ read large). The volume 68 titles give nothing, mostly because manga-ocr misreads the title text itself, so no kanji matches. No horizontal block without furigana got a reading on any page of the three sets. One synthetic case does fool it: a line of kana at under 0.6 of the text size, set right above a line of kanji, is read as furigana; none of the three sets has such a block. Horizontal blocks cost about 85 ms each on Naruto volume 1 with manga-ocr on the GPU. The three grouping rules also moved vertical readings: on 30 pages of volume 68 the count went from 233 to 243, and most changed readings became whole words (砂漠, 大地, 一尾, 世界) instead of one-kanji fragments or misreads, while a few got worse.

**Names of one kanji.** Most one-kanji words with furigana are ordinary words, so a one-kanji reading is stored only when the kanji, not after another kanji or a polite お, ご or 御, is followed by an honorific in the Japanese, on this page or an earlier page of the work, the English of that line spells the reading with the same honorific (枢さん and Kururu-san), and the reading has at least 2 kana. On the final runs below, the bench lines carried 1,497 one-kanji readings; the English spelled 15 of them as a capitalised word (術 as Jutsu, 里 as Sato, 剣 as Ken and so on) and 7 had an honorific after them in the Japanese, none of them both. The rule stored none, and none should be stored: these sets have no one-kanji name with furigana. The only one-kanji name in the references, 枢 in OpenMantra, is read inline as 枢（くるる）. An earlier version without the お/ご and 2-kana checks stored one: a polite word for a relative whose furigana was misread as a single kana, which the model then wrote as a name with -sama.

**Names and terms.** A furigana entry's kind comes from evidence already on its lines. A technique suffix on the kanji (術, 眼, 剣, 国, 流, 校, 法, 拳, 陣, 獣, 力, 忍, 刀) counts two votes for `term`, and so does "the", "a", "this", "his" and the like before the English word, or quotes around it, one each. An honorific after it in either language, "'s" after it, or the word standing alone or as an address counts for `name`. A tie is `name`. 丸 is not a term suffix here: 大蛇丸, 赤丸 and 華鏡丸 are characters and only 螺旋丸 is a technique. Surname endings such as 村, 林 and 山 are left out for the same reason. On every entry learned by the final runs below, checked by hand: name→name 14, name→term 1, term→name 11, term→term 18, plus one ordinary phrase that is neither. A `term` label was right 18 of 19 times; the mistakes are mostly terms with no signal at all, and titles such as 火影 (Hokage-sama) that take an honorific like a person. The entries with no signal split 6 characters to 5 terms, and runs on Naruto volume 68 with every entry forced to `name` (COMET 0.7768), forced to `term` (0.7739) and classified (0.7735) are all within noise, so a tie defaults to `name`, which is also what an entry saved before kinds existed reads as. The reader can change any kind, and theirs wins.

**Renames keep one entry per character.** Renaming a name in the cast sheet used to make a second entry beside the first. Editing an `auto` entry saved a `user` name with no Japanese, which the server could match to the `furigana` name of the same character only by its exact English spelling, so after a respelling both showed. And after a `furigana` name was respelt to something unlike its old spelling, the old `auto` spelling came back. The rule that should have hidden it, the `auto` line holding the saved name's Japanese before the same honorific, never fired: the `auto` spelling reached it without its honorific, so it looked up no honorific kana, and only the 0.8 spelling similarity ever hid anything. Now the server keeps each `auto` spelling's honorific and reads from its lines the Japanese right before that honorific's kana (the `guess`), and a stored name hides an `auto` one when its Japanese is the guess, its reading romanizes to the spelling, or its current or any earlier spelling is close. Stored names keep their earlier spellings in `was`, at most 8, so a replaced spelling stays hidden, a page that used it is still found stale, and two stored names of one character are merged when saved. The translator still gets only the current spelling. The guess takes kana only when they sound like the English name and gives up when two different kanji runs carry the same honorific on one line, because a wrong guess would hide another character's `auto` name; the earlier wording, any saved Japanese followed by any honorific in the line, would hide `Yuki` behind 凛恋 on a line such as 凛恋ちゃんとユキちゃん. Nothing was benchmarked: this changes which names the translator is sent only for works where a duplicate or a replaced `auto` spelling used to be listed, and those lists now hold one spelling per character.

**Tried and rejected.** Sending only readings of kanji followed by an honorific. On Naruto volume 68 that kept 1 of 1,120 readings, because names there appear bare. Storing every name of one kanji: on Naruto volume 68 they were mostly ordinary words the model happened to romanize, and one was a reading attached to the wrong kanji. Searching a strip above a horizontal block for furigana outside its box: it made no difference, since the detector's box already holds the furigana.

## Retranslating reads the page again

**Decision.** `POST /v1/pages/{page_id}/retranslate` and the redo of a page whose names changed take the original image again and run detection, balloons, OCR, furigana and erasing on it once more, then letter the page with the new English for the picked bubbles and the cached English for the rest. The cache keeps only the rendered page and `result.json`, as before. The re-read bubbles are matched to the cached ones by box overlap (at least 0.5 IoU, the same source first), and a bubble that matches none is translated fresh. A retranslated line is marked in `result.json` and wins over the same line on other pages when the line memory is asked, so the reader's fix is what later pages reuse, also after a restart. A remembered line that holds the `ja` of a `user` or `furigana` name but not its spelling is translated again instead of reused.

**Why.** Typesetting needs more than the erased page: it needs each bubble's text mask, balloon mask and how bubbles share a balloon, so keeping the cleaned page alone would not save the re-read. Keeping both costs disk on every page for a rare action. On 2026-10-28, `reread_cost.py` (kept with the run notes, not in the repo) read ten OpenMantra pages, 010 and 020 of each work, three times each on the CPU with the machine busy (load average 8 to 24):

| Per page, median of 10 | Value |
| --- | ---: |
| Detection, balloons, OCR and furigana, CPU | 2.5 s |
| Erasing, CPU | 0.08 s |
| Erased page, lossless WebP | 366 KB |
| Erased page, WebP quality 90 | 186 KB |
| Bubble masks and layout, pickled and compressed | 8 KB |

The same pages took 1.4 to 4.6 s on a quiet CPU in [performance.md](performance.md#where-a-pages-time-goes), and the GPU timings there put detection, OCR and erasing at about 0.35 s, beside a translate call of 0.6 to 1.4 s that a retranslate makes anyway. Keeping the erased page losslessly with its masks would add about 374 KB to an entry whose median is 223 KB, so the default 2 GiB cache would hold about 3,600 pages instead of 9,600. WebP at quality 90 halves that but encodes the page a second time, and pickled masks tie the cache to the code that wrote them. Every one of the 30 reads found the same bubbles, box for box and line for line, so matching by box loses nothing on one machine.

**Tried and rejected.** Nothing was built for the kept erased page; the numbers above decided it.

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

## Only the extension may call the server, with no token

**Decision.** The server refuses a request when its `Origin` is anything but `chrome-extension://`, when it has no `Origin` but its `Sec-Fetch-Site` is `cross-site` or `same-site`, when its `Host` is not `127.0.0.1`, `localhost` or `[::1]`, or when it comes from another machine. `YOMIKAE_EXTENSION_ID` can pin the accepted extension ids. There is no token and nothing to set up.

**Why.** Any web page can send a multipart `POST` to `127.0.0.1` without a CORS preflight, and DNS rebinding makes a page same-origin with the server, so CORS alone let every site you visit translate images, file reports, rewrite a work's names through a retranslate, and, after rebinding, read names and results and clear the cache. Browsers set `Origin` and `Sec-Fetch-Site` themselves and pages cannot forge them. Measured in Chromium on 2026-10-28: the extension's `POST`s carry `Origin: chrome-extension://<id>`, its `GET`s carry no `Origin` and `Sec-Fetch-Site: none`, and a page's no-cors `fetch`, form `POST` and `<img>` loads carry a web `Origin` or `Sec-Fetch-Site: same-site`/`cross-site`. No content script talks to the server, because the background worker fetches each rendered page and hands the bytes to the tab, so refusing every web-page request breaks nothing. `extension/tools/e2e-local.mjs` checks this in a real browser.

**Tried and rejected.** A per-install token. The server would have to hand it to the extension, which cannot read files, so you would copy it from the server's log into a settings page the extension does not have, and paste it again after every reinstall. The header checks need no setup and block the same requests from web pages. What they leave open is another installed extension that has access to `127.0.0.1`. Setting `YOMIKAE_EXTENSION_ID` stops every request of its other than a `GET`, but not its `GET`s, which carry no `Origin` from any extension, so it can still read names and rendered pages.

**Known limit: names that resolve to local addresses.** The extension decides whether an image is on this computer, the local network or the web from the host name in its URL, and after redirects from the host name the redirects end at. A web name that resolves to a private address, such as `cam.attacker.example` pointing at `192.168.1.1`, passes as the web, so a page on an enabled site can still have the extension fetch from the local network that way. A `fetch` response does not say which address it came from. `chrome.webRequest.onResponseStarted` does, in `details.ip`, so the worker could compare that address against the tab's place and drop the bytes. That needs the `webRequest` permission, a wider grant than the extension asks for today, and it would still send the request before it could refuse the answer. It was left out on 2026-10-28 until it is worth that cost. Until then, enable Yomikae only on sites you trust.

## Uploads are decoded only as web image formats, with Pillow 13

**Decision.** The server opens uploads with Pillow's JPEG, PNG, WebP, GIF and AVIF plugins only, and `pyproject.toml` requires Pillow 13.0 or newer.

**Why.** Pillow 12.3 has 44 advisories fixed in 13.0, among them heap overflows in the JPEG, FlashPix and TIFF decoders and a Ghostscript argument injection through EPS. The extension posts whatever image a site serves, so a hostile site reaches the decoder through the user's own extension, and the request checks cannot stop that. Limiting the plugins keeps rare formats away from their decoders on any Pillow version. Measured on 2026-10-28: the full server suite passes on Pillow 13.0.0, and six synthetic pages rendered through the fake translator came out byte-identical on 12.3.0 and 13.0.0, so the upgrade changes no output. An existing venv keeps its Pillow until you reinstall or run `uv pip install -U pillow`.

**Pixel limit.** The server refuses an upload of more than 40,000,000 pixels before decoding it, rather than Pillow's default of 89,478,485. A decoded page takes 3 bytes a pixel, and the pipeline holds several copies of it, so at Pillow's limit one copy is 268 MB and one request could take a gigabyte or more. The largest page among the 618 benchmark pages used in development is 3040x2400, 7.3 million pixels, and no adapter in the repo serves long strips. 40 million keeps an 800x50000 webtoon strip, the tallest the limit was meant to allow, with room for pages five times the largest seen.

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
