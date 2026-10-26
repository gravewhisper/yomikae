# Typesetting notes: rules from AnonBlack's guide, mapped to our typesetter

Source: "AnonBlack's Typesetting Guide" on MangaDex,
https://mangadex.org/title/08e1f85a-bb12-4fe4-aec5-0d7a80b3a261/anonblack-s-typesetting-guide

Everything below is paraphrased. I read these chapters: 0 (font guides 1 to 5 plus the shouts addendum, 6 pages),
1 (101 Basics), 2 (102 More Skills), 3 and 3.1 (201 Lining and its answers), 4 (202 Special Effects),
5 (203 More Effects), 6 (301 Saving Time), 7 (302 Breaking the Rules), 8 and 8.1 (303 Detailed Placement and a
reader's alternative for cut-off text), 9 (304 Font Choice), 10 (Translator's Notes PSA), and 12 (Splitting
Sentences). That is 27 page images. Chapters 11, 13 and 14 are about translation (puns, katakana, localization),
not typesetting, so I skipped them. Chapters 4 to 6 are mostly Photoshop how-tos; I kept only the rules in them.

Where a threshold below is my own suggestion and not from the guide, it says "(suggested)".

Code references are to `server/yomikae/typeset.py`.

---

## 1. Line breaking ("lining")

The guide calls this its most important skill. It says good lining is what separates good typesetters from bad
ones. It has two parts: the overall shape of the block, and where each break falls.

### 1.1 Shape the block like a rounded diamond, widest in the middle

- **Rule:** The block's line widths should grow toward the middle and shrink toward the top and bottom. A plain
  rectangle with even line widths is the accepted alternative. Avoid ragged, irregular outlines such as a long
  line, then a short one, then a long one.
- **Why (guide):** A balanced block looks settled in the bubble. In the guide's A/B/C example the same text
  is set three ways, and only the diamond version is called correct.
- **Algorithm:** Score each candidate set of breaks by its width profile `w[0..n-1]`.
  - Penalize any break in unimodality. Each time the widths go down and then up again, add a cost proportional to
    the size of the rise.
  - Penalize the gap to a target profile. The target is either the bubble's own row span at that line
    times a fill factor, or a lozenge `w_max * (1 - k*|i - mid|/mid)` with k of about 0.35 to 0.5 (suggested).
  - A near-flat profile, where every width is within 10% of the median, should also score well. That is the
    "block" option.
- **Ours:** Partial. `wrap()` is greedy. It fills each line up to the mask's row span, so in an oval bubble the
  result is roughly a diamond, but only because the bubble is round. The last line is often short, and the line
  before a forced break can be stubby. Nothing scores shape, and in rectangular boxes we produce a ragged
  first-fit block.

### 1.2 Break at punctuation first

- **Rule:** When the text has several sentences or clauses, put the line breaks right after `. , ! ? … —` rather
  than in the middle of a clause.
- **Why (guide):** Readers pause at the end of every line anyway. A break that lines up with a pause feels
  natural, and a break inside a phrase reads awkwardly.
- **Algorithm:** Give each break opportunity between words a cost. A break after sentence-final punctuation
  costs 0, after a comma, ellipsis or dash about 1, and anywhere else the phrase cost from 1.3.
  Then pick breaks with dynamic programming (Knuth-Plass style). The total cost is shape cost (1.1) plus break
  costs plus orphan cost (1.4). The candidate line counts come from the bubble, as they do now.
- **Ours:** No. The greedy wrap ignores punctuation.

### 1.3 Break at grammatical phrase boundaries

- **Rule:** Inside a clause, break between grammatical units: subject | verb phrase | object | prepositional
  phrase. Don't strand a word that only makes sense together with the next one.
- **Why (guide):** English grammar says which words belong together, and lines that follow it read
  effortlessly. The guide's example sets "I LIKE / THE WAY / YOU MOVE." rather than splitting "the way".
- **Algorithm:** A cheap heuristic needs no parser.
  - High cost for a line that ends in a function word: articles (a, an, the), prepositions (of, to, in, on,
    at, for, with, from, by), conjunctions (and, or, but, that, if), possessives (my, your, his, her, their),
    auxiliaries (is, are, was, will, can, do, don't), and pronoun subjects (I, we, you) when a verb follows.
  - Medium cost for splitting a number from its unit, or a title from a name.
  - Low cost before a conjunction or preposition, since breaking before "and" or "to" is good.
  - A dependency parse (spaCy) would be better, but the word list gets most of the benefit.
- **Ours:** No.

### 1.4 No orphans: no lone word, and never lone punctuation or a tilde

- **Rule:** Punctuation never sits alone on a line, which includes `?!`, `!!` and `…`. A tilde always stays
  attached to the word before it and never starts a line. Avoid one short word alone on a line. The exception
  is a very skinny bubble, see 3.1.
- **Why (guide):** A lone punctuation mark looks silly and hurts reading; the guide compares a lone tilde to a
  lone period. Hearts, stars and notes are the only symbols that may sit on their own line, see 5.1.
- **Algorithm:**
  - Before wrapping, glue tokens. Attach any token made only of punctuation or `~` to the previous word with a
    non-breaking join. Also attach a word to a following `…` or `--`.
  - Add a high cost (suggested) for a first or last line that holds one word of 3 characters or fewer, or
    whose width is under 35% of the widest line.
- **Ours:** Partial. Words are split on whitespace, so "OKAY ~", "what ?!" or "... " can wrap onto their own
  line. `fonts.clean()` collapses runs of repeated `~!?` but does not glue them to the word.

### 1.5 Hyphenation is a last resort, and only at syllables

- **Rule:** Don't split words if you can avoid it, because rewrapping usually removes the need. When a split is
  unavoidable (a long word in a narrow bubble), break at a syllable, ideally at a prefix or suffix boundary
  ("antidis-establish-mentarian-ism"). Never break in a way that misleads, like "IMP-ROVING" or "DOE-SN'T".
- **Why (guide):** Badly placed hyphens make the reader re-parse the word. The guide calls its own bad example
  a mess.
- **Algorithm:**
  - Keep hyphenation off by default. Only allow it when no unhyphenated layout fits at or above a minimum
    readable size, for example 75% of the page size (suggested). Today we allow it once every size down to
    MIN_SIZE has failed, which is close to this.
  - Get legal break points from a hyphenation dictionary (pyphen, `en_US`). Require at least 2 letters before
    the break and 3 after.
  - Never split inside a contraction, around an apostrophe, or in a word of 5 letters or fewer.
  - Prefer known affix boundaries: un-, re-, dis-, -tion, -ment, -ness, -ing.
- **Ours:** Partial. Hyphenation is correctly a fallback, but `wrap()` cuts at the largest character position
  that fits, which gives breaks like "IMP-" plus "ROVING". It can also split contractions and short words.

### 1.6 Never let a "text box" do the lining

- **Rule:** Set every line by hand. Don't paste text into a fixed-width box and let it flow.
- **Why (guide):** An auto-flowed box produces a rectangle-ish block with arbitrary breaks. The guide calls this
  "the devil".
- **Algorithm:** This is the summary of 1.1 to 1.5. A typesetter that only knows "fill the available width"
  is a text box. Replace first-fit with an optimizer over breaks.
- **Ours:** No. Our greedy fill against the mask is a text box that happens to be shaped like the bubble.

## 2. Size and placement

### 2.1 Size per bubble: room to breathe, but not swimming

- **Rule:** Text should be neither so big that it crowds the outline nor so small that the bubble looks empty.
  The guide explicitly says it is fine to change the size from bubble to bubble.
- **Why (guide):** Crowded text looks cramped and tiny text looks lost. The guide calls this the Goldilocks rule.
- **Algorithm:**
  - Measure fill as the area of the text's ink bounding box divided by the area of the bubble interior. Aim for
    roughly 35 to 60% (suggested).
  - Keep a minimum clear margin between the glyphs and the outline of about 0.5 to 0.8 of the cap height
    (suggested).
  - Keep a page-level consistency term, but make it soft. Within one text role (dialogue), keep sizes within
    about ±20% of the page median (suggested), rather than capping every bubble at the median.
- **Ours:** Partial. We fit the largest size per bubble, then cap every bubble at the page median and at 32 px.
  Small bubbles end up correctly sized. Big bubbles get pulled down to the median and can swim with too much
  empty space. The guide favors per-bubble sizing, while our docstring takes the opposite view ("text on a page
  reads at one size"). A soft band keeps the consistency without leaving large bubbles half empty.

### 2.2 Shouts are big

- **Rule:** Don't set exclamations too small. Compare with the raw to see how much bigger the mangaka drew them.
- **Why (guide):** A shout has to look loud, and small text reads as a mumble.
- **Algorithm:**
  - Detect a shout: the translation ends in `!` or `!?`, is short (say 1 to 4 words), or the source glyphs are
    larger than the page's median source glyph height (OCR box height divided by character count). The bubble
    shape helps too: spiky outlines have high perimeter²/area compared with an ellipse.
  - Exempt shouts from the page-median cap, letting them go up to MAX_SIZE × about 1.5 (suggested).
  - Match the source glyph ratio: `size_en ≈ page_size × (src_glyph_h / median_src_glyph_h)`, clamped.
- **Ours:** No. Shouts are capped at the same page target as ordinary dialogue, which is exactly what the guide
  warns against.

### 2.3 Center the block in the bubble, both ways

- **Rule:** Center each line horizontally, and center the whole block vertically: not too high, not too low.
  Start every new text block centered.
- **Why (guide):** Off-center text looks careless. The guide treats center alignment as mandatory.
- **Algorithm:** Center horizontally per line on the bubble's axis. Center vertically on the ink bounding box,
  from the cap height of the first line to the baseline of the last line, ignoring descenders and trailing
  leading. Put it at the centroid of the bubble interior, or at the center of the largest inscribed circle for
  lopsided bubbles. Check that the top and bottom margins differ by less than about 10% (suggested).
- **Ours:** Mostly yes. The block is centred on the interior's centroid, and on the largest inscribed circle only when
  the lines do not fit at the centroid. Choosing whichever of the two lined better put text at the widest row of
  egg-shaped bubbles, 58 px below the centroid on one reported page. Lines are centered on `cx`. The block is
  centered on the line-box stack, which uses ascent plus descent times 1.12. With all-caps text the visible ink sits a little high, because the descender
  space at the bottom is empty. Comic Neue's ascent also includes accent room. It is a small offset, but
  visible in short bubbles.

### 2.4 Leading

- **Rule:** The guide never states a leading value. Its examples consistently use tight leading for all-caps
  comic fonts, with lines nearly touching, about 1.0 to 1.1 of the cap-plus-gap height.
- **Algorithm:** For all-caps text, base the line pitch on cap height, not on the font's ascent plus descent.
  For example `pitch = cap_height × 1.35` to `1.5` (suggested). This also makes 2.3 easier.
- **Ours:** `LINE_SPACING = 1.12 × size`. That is reasonable for mixed case, but loose for all caps, since no
  descenders use the space.

### 2.5 Padding inside the bubble

- **Rule:** This is implied by 2.1 and by the examples: text never touches the outline, and the margin grows
  with the bubble.
- **Algorithm:** Erode the interior mask by a margin proportional to the font size, for example 0.4 × size
  (suggested), rather than only proportional to the bubble's dimensions. The glyph-to-outline distance then
  looks the same across bubbles.
- **Ours:** Partial. We erode by `max(3, 7% of min(w, h))`, a margin tied to the bubble's size, not the text's.
  Big bubbles get a large dead margin and small bubbles a very thin one.

## 3. Bubbles that don't cooperate

### 3.1 Skinny bubbles

- **Rule:** For very tall, narrow bubbles, set one word per line and hyphenate longer words. If that still
  doesn't fit, let the text spill past the bubble edge and give it a stroke. In tiny bubbles it is acceptable,
  though not preferred, to put the punctuation on its own line. A narrower font helps for shouts in skinny
  bubbles. Never shrink the text until it is tiny unless there is truly no other option.
- **Why (guide):** Tiny text is hard to read, and readable but spilled text is the lesser evil.
- **Algorithm:**
  - When the best fit is below a floor such as 0.7 × the page size (suggested), try in this order:
    (a) one word per line with syllable hyphenation;
    (b) allow the layout to exceed the mask by up to about 15% of the bubble width on each side, and draw a
    white stroke on the part outside;
    (c) only then shrink.
  - For narrow bubbles, also try a condensed variant of the font.
- **Ours:** Partial, on purpose. Below the floor (0.75 × the page target) a bubble tries, in order, hyphenation,
  then the dialogue face squeezed to 80% and then 70% (whole words, then hyphenated), and only then shrinks, down
  to MIN_SIZE = 10. Everything stays inside the bubble. Step (b), the stroked spill past the edge, is
  deliberately not used: the owner prefers a clean outline with smaller lettering over bigger text that runs past
  the bubble or onto the art. The art fallback widens tall vertical blocks (`target_w = h × 0.5`).

### 3.2 Text over panel borders and onto art

- **Rule:** The guide doesn't cover panel borders directly. Its only related rule is that any text spilling out
  of a bubble or sitting on art gets a stroke so it stays readable.
- **Algorithm:** Any glyph pixel whose background is not the bubble interior (art, a border, or a neighboring
  panel) gets the stroke treatment from 4.1. Compute this per line or per pixel, not per region.
- **Ours:** Partial. The stroke is all-or-nothing per region: on art or on a busy background. Text set in a
  bubble never leaves it (3.1), so it needs no stroke.

### 3.3 Text cut off by an overlapping bubble or object

- **Rule:** When a background bubble is partly covered, set its text as if it were complete, then hide the part
  under the foreground bubble or object, using a mask so the text stays editable. Work out with the translator
  which part of the English corresponds to the hidden part. Japanese lines run right to left, so the hidden part
  of the original may be the end of the sentence or pieces from its middle.
- **Why (guide):** It keeps the original's layering effect.
- **Algorithm:** Detect bubbles whose outline is broken by another closed bubble. Lay the text out in the
  completed shape, for example the convex hull or an ellipse fit, then clip it with the foreground bubble mask.
- **Ours:** No. The flood fill finds only the visible part and fits the text there. That is a safe default:
  it never hides words, so lean toward it.

## 4. Strokes and effects

### 4.1 White stroke for text on art, and keep it modest

- **Rule:** Text over art gets an outside stroke, usually white, sized to the text. The guide marks the stroke
  size field with "don't make it too big". Its example uses about 4 px on ordinary text.
- **Why (guide):** The stroke separates the letters from busy screentone and lines. Too thick, and it becomes a
  blob.
- **Algorithm:** `stroke = clamp(round(size × 0.10 to 0.14), 2, 6)` (suggested). Draw black text on a white
  stroke, or white on black when the background is dark. Choose by the mean luminance under the text after
  dilation, not under the original block.
- **Ours:** Yes. `max(2, size // 8)` gives 4 px at 32 px, and the dark or light choice matches. Good.

### 4.2 Copy double strokes and patterned text from the raw

- **Rule:** If the original text has a double stroke (for example a white inner and a black outer), a screentone
  pattern fill, or a patterned stroke, reproduce it.
- **Why (guide):** It stays true to the original page.
- **Algorithm:** Mostly out of scope for automatic work. A cheap version: when the source text was stroked,
  measured as a thick ring of the opposite color around the glyphs before inpainting, add a thin black outer
  ring around the white stroke.
- **Ours:** No (low priority).

### 4.3 Curved and angled surfaces

- **Rule:** Text on a curved banner should follow the curve (an arc or flag warp). Text on a flat surface seen
  at an angle (a phone screen, a sign) should be rotated, then skewed to fit the surface's edges. Never just
  rotate it.
- **Why (guide):** Upright or merely rotated text on a slanted surface looks pasted on.
- **Algorithm:** For a quadrilateral surface, find the four corners of the region, as `cv2.minAreaRect`
  can't capture skew. Lay the text out flat, then apply a perspective transform (`cv2.getPerspectiveTransform`)
  onto the quad.
- **Ours:** No (low priority, signs only).

## 5. Symbols and punctuation

### 5.1 Hearts, stars and music notes

- **Rule:** At the end of a large block, put the symbol after the last word. At the end of a small block, put
  it centered on its own line below. The guide's author prefers the second. Inside a sentence, keep it inline.
  Any symbol source is fine, as long as it looks right.
- **Algorithm:** If a symbol ends the text and the block has 3 or fewer lines, emit it as its own centered final
  line, at the same size or about 0.9× (suggested). Otherwise glue it to the last word.
- **Ours:** Partial. The symbol font fallback draws the glyphs, but placement follows the greedy wrap.

### 5.2 Punctuate dialogue, but allow deliberate omission

- **Rule:** English dialogue needs punctuation on every sentence. Japanese often omits it, and the translation
  shouldn't copy that. The exception is a deliberate dramatic effect: a missing final period for a pregnant
  pause, or no punctuation across a sentence split over many bubbles in somber scenes.
- **Algorithm:** This is a translation-stage decision. The typesetter should never strip punctuation. At most,
  add a lint step that flags a dialogue bubble ending in a bare word while its neighbors are punctuated.
- **Ours:** N/A for typeset.py. Check the translation prompt instead.

## 6. Fonts and casing

### 6.1 Use a comic lettering font, never Comic Sans or a plain body font

- **Rule:** Ordinary dialogue uses a comic lettering font. Its examples: Wild Words, CC Astro City, Anime Ace,
  VTC Letterer Pro, Wild and Crazy. The guide says never to use Comic Sans, and that non-comic fonts (Arial and
  similar) are boring.
- **Why (guide):** Lettering fonts are the standard look of scanlations.
- **Algorithm:** Pick a free, embeddable font with the classic all-caps lettering look, for example Anime Ace
  (Blambot, free for non-commercial use) or Komika. Check the license.
- **Ours:** Partial. Comic Neue is a comic-style font and not Comic Sans, but it is a mixed-case, Comic Sans–like
  design rather than a lettering font.

### 6.2 All caps for normal text

- **Rule:** The guide never states this as a rule, but nearly every example in its dialogue fonts is uppercase.
  It lists the "normal text" fonts separately from a smaller "lowercase normal text" category, which it treats
  as a style choice for particular series. Handwritten side text and character handwriting are mixed case.
- **Why:** All caps is the comic lettering convention. Lettering fonts are drawn for caps, and they look more
  like the original's even block of kana.
- **Algorithm:** Uppercase dialogue, narration and shouts at render time. Keep the translation itself in mixed
  case, and keep handwriting and side-note roles mixed case. Most lettering fonts use the lowercase slots for
  small caps, so if the chosen font has real lowercase, convert with `str.upper()`. Watch for the "I" crossbar:
  some lettering fonts use the crossbarred I only for the pronoun.
- **Ours:** No. We render the translation's casing in Comic Neue.

### 6.3 Pick fonts by role, not by character

- **Rule:** Choose a small set of fonts, one per role, and stick with them for the whole series:
  - main body (dialogue and ordinary thoughts);
  - shouts;
  - loud or pained shouts (rougher, messier);
  - handwritten side text (small notes beside bubbles);
  - deep thoughts or narration in serious series (a cleaner, "cooler" face; the author uses CC Astro City);
  - a specific character's handwriting (letters, notes);
  - SFX and titles.

  Don't give each character a font. Don't use two similar-looking fonts for different roles. Do switch fonts,
  or just use italic, when the mangaka switched fonts in the raw.
- **Why (guide):** One font for everything is flat ("BO-RING"). Too many fonts look chaotic. Similar fonts in
  different roles make the page look plain, because the distinction is lost. Consistency across chapters looks
  professional.
- **Algorithm:**
  1. Classify each bubble's role from cheap signals.
     - Shout: spiky outline, high perimeter²/area; `!` ending; large source glyphs.
     - Thought: cloud outline, many small convex bumps, or a bubble with no tail.
     - Narration: a rectangular box, where the polygon fit has 4 corners and fills more than 90% of the bbox.
     - Side text: no enclosing bubble (our `on_art`), small source glyphs, a short phrase.
     - Whisper or aside: small source glyphs or a dashed outline.
  2. Map role → (font, style):
     - dialogue → regular lettering;
     - shout → bold or heavier lettering, larger (2.2);
     - thought or narration → italic, or a cleaner face;
     - side text → a small handwriting font in mixed case;
     - whisper → smaller, or a lighter weight.
  3. Fix the mapping per series and save it in config, so every chapter reuses it.
- **Ours:** No. There is one font for all roles.

### 6.4 Bold and italic as cheap role signals

- **Rule:** If you don't want to add a font, change the style. Use bold or italic for shouts, sighs and similar
  lines. The guide's own example uses only italic to mirror the raw's font switch.
- **Algorithm:** This is the minimal version of 6.3: bold for shouts, italic for thoughts, narration and
  off-panel speech. The Comic Neue family includes Regular, Bold and Italic styles, but only `server/fonts/ComicNeue-Bold.ttf`
  is in the repo, so we would need to add those files.
- **Ours:** No. We always use Bold. That makes the bold-for-shout signal unavailable. Switching dialogue to
  Regular, or to a lettering font's normal weight, would free it.

## 7. Content hygiene (guide chapters 10 and 12)

- **Translator notes:** Keep them short. Explain a pun, a cultural reference or untranslatable wordplay, and set
  them small near the panel edge. Never add commentary or reactions ("lol", "XD"); the mangaka didn't put them
  there. Relevant only if our pipeline ever adds notes. They should go in a small font in the margin, never in
  bubbles.
- **Sentences split across bubbles:** Japanese often splits one sentence over two or three bubbles, with the
  object before the verb. English must reorder the words so each bubble reads naturally, while keeping the
  emphasis in the bubble the original stressed. This belongs to the translation stage: translate all of a
  page's bubbles together, in reading order, and let the model redistribute words across consecutive bubbles of
  one speaker.

---

## Priorities: the changes that would most improve how naturally our output reads

Status: all five are implemented in `server/yomikae/typeset.py` and `server/yomikae/lining.py`. The "Ours"
lines above describe the typesetter before them. The lettering face is Anime Ace 2 when `server/fonts/animeace2_reg.ttf`
is present, else Shantell Sans Medium (OFL), because Anime Ace's license does not allow redistribution here; shouts use Bangers. The stroked spill past the bubble edge (3.1 b)
is deliberately not used, so text stays inside its bubble.

1. **Replace greedy wrapping with break optimization (rules 1.1 to 1.4).** Use dynamic programming over break
   points. The cost combines punctuation and phrase boundaries (break after `.,!?…`, never end a line on
   "the/a/to/of/my"), a diamond or bubble-span shape target, and orphan penalties. Glue punctuation, `~` and
   ellipses to their word first. This is the guide's top skill, and it affects every bubble on every page.
2. **Use all caps with a real lettering font (6.1, 6.2).** Uppercase dialogue at render time, in a lettering
   face such as Anime Ace instead of mixed-case Comic Neue Bold. Tighten leading to cap height (2.4) and center
   vertically on the cap-height ink box (2.3). This is the most visible "this looks like a scanlation" change.
3. **Detect shouts and size and style them (2.2, 6.4).** Exempt shouts from the page-median cap, scale them by
   source glyph size, and render them bold or heavy, with regular weight for normal dialogue. Today shouts come
   out the same size as mumbles.
4. **Make size consistency soft, not a hard cap (2.1, 2.5).** Keep each role's sizes within about ±20% of the
   page median instead of forcing every bubble down to it. Make inner padding proportional to the font size.
   Large bubbles stop looking empty, and small ones stop looking crowded.
5. **Handle skinny bubbles and hyphenation properly (1.5, 3.1).** Use syllable-aware hyphenation (pyphen)
   instead of character cuts, with no splits inside contractions or short words. When the fit drops well below
   the page size, prefer one word per line, then a stroked spill past the edge, before shrinking toward 10 px.
