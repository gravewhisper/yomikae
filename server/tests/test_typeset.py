from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

from yomikae import detect, typeset
from yomikae.config import FONTS_DIR, Settings
from yomikae.model import Balloon, Box, Bubble, TextBlock

BUNDLED = (FONTS_DIR / "ShantellSans-Medium.ttf", FONTS_DIR / "Bangers-Regular.ttf")
FONTS = typeset.load_lettering("upper", BUNDLED)


def bubble_page(ellipses: list[tuple[int, int, int, int]], size=(1200, 900)) -> np.ndarray:
    page = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(page)
    draw.rectangle((0, 0, size[0] - 1, size[1] - 1), fill=(120, 120, 120))
    for e in ellipses:
        draw.ellipse(e, fill=(255, 255, 255), outline=(0, 0, 0), width=4)
    return np.asarray(page)


def bubble_in(e: tuple[int, int, int, int], text: str, source: str = "どこへ行くつもりなの") -> Bubble:
    x0, y0, x1, y1 = e
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    box = Box(cx - 20, cy - 80, cx + 20, cy + 80)
    return Bubble(TextBlock(box, True, box, np.zeros((box.h, box.w), np.uint8)), source, text)


def test_same_size_bubbles_with_similar_text_get_one_size():
    ellipses = [(40 + i * 280, 60, 280 + i * 280, 400) for i in range(4)]
    texts = ["Where are you going now?", "I told you to wait here.", "We promised, didn't we?", "Say that one more time."]
    sources = ["どこへ行くつもり", "待ってと言った", "約束したでしょう", "もう一度だけ言って"]
    _, sizes = typeset.typeset(FONTS, bubble_page(ellipses), [bubble_in(e, t, s) for e, t, s in zip(ellipses, texts, sources)])
    assert len(set(sizes)) == 1, sizes


def test_narrow_bubble_is_never_larger_than_its_neighbours():
    wide = [(40, 60, 300, 400), (340, 60, 600, 400), (640, 60, 900, 400)]
    narrow = (960, 60, 1080, 400)
    ellipses = wide + [narrow]
    texts = ["Where are you going now?", "I told you to wait here.", "We promised, didn't we?", "Huh?"]
    sources = ["どこへ行くつもりなの", "ここで待ってと言った", "約束したでしょう", "え？"]
    _, sizes = typeset.typeset(FONTS, bubble_page(ellipses), [bubble_in(e, t, s) for e, t, s in zip(ellipses, texts, sources)])
    assert sizes[3] <= min(sizes[:3]), sizes


def test_short_line_in_a_big_bubble_does_not_balloon():
    big = (100, 100, 900, 800)
    _, sizes = typeset.typeset(FONTS, bubble_page([big], size=(1000, 900)), [bubble_in(big, "And...", "そして…")])
    assert sizes[0] <= 40, sizes


def test_heart_and_note_render_as_real_glyphs():
    e = (100, 100, 500, 500)
    page = bubble_page([e], size=(600, 600))
    renders = {}
    for symbol in ("♡", "♥", "♪"):
        rendered, sizes = typeset.typeset(FONTS, page, [bubble_in(e, symbol, "♡")])
        assert sizes[0] > 0, f"{symbol} was dropped"
        changed = np.abs(rendered.astype(int) - page.astype(int)).sum(2) > 0
        assert changed.sum() > 50, f"no ink for {symbol}"
        renders[symbol] = rendered
    assert (renders["♡"] != renders["♥"]).any() and (renders["♥"] != renders["♪"]).any()
    text = typeset.typeset(FONTS, page, [bubble_in(e, "I love you ♡", "♡")])[0]
    plain = typeset.typeset(FONTS, page, [bubble_in(e, "I love you", "♡")])[0]
    assert (text != plain).any(), "the heart changed nothing"


def ink(rendered: np.ndarray, page: np.ndarray) -> np.ndarray:
    return np.abs(rendered.astype(int) - page.astype(int)).sum(2) > 0


def test_lines_sharing_one_frame_never_overprint():
    frame = (60, 60, 800, 460)
    page = bubble_page([frame])
    columns = [Box(cx - 30, 110, cx + 30, 410) for cx in (560, 430, 300)]
    texts = ["Look clean!", "Act like a composed man!", "Compliment the girl without hesitation!"]

    def render(shown: int) -> np.ndarray:
        bubbles = [
            Bubble(TextBlock(box, True, box, np.zeros((box.h, box.w), np.uint8)), "清潔感を出せ！", text if shown == i else "")
            for i, (box, text) in enumerate(zip(columns, texts))
        ]
        return typeset.typeset(FONTS, page, bubbles)[0]

    alone = [cv2.dilate(ink(render(i), page).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0 for i in range(3)]
    assert all(a.any() for a in alone)
    for i in range(3):
        for j in range(i + 1, 3):
            assert not (alone[i] & alone[j]).any(), f"{texts[i]!r} and {texts[j]!r} overlap"
    for a, box in zip(alone, columns):
        cols = np.nonzero(a.any(0))[0]
        assert cols.min() <= box.center[0] <= cols.max(), "text drifted away from its line"


def test_a_roomy_bubble_grows_but_stays_within_the_page_band():
    small = [(40 + i * 230, 60, 250 + i * 230, 360) for i in range(4)]
    big = (60, 420, 900, 880)
    texts = ["Where are you going now?", "I told you to wait here.", "We promised, didn't we?", "Say that one more time."]
    long_text = "If you keep running off like that, nobody will be left to come looking for you next time, you know."
    block = Box(380, 560, 580, 740)
    bubbles = [bubble_in(e, t) for e, t in zip(small, texts)]
    source = "そうやってすぐどこかへ行っちゃうと、次は誰も探しに来てくれなくなるよ。"
    bubbles.append(Bubble(TextBlock(block, True, block, np.zeros((block.h, block.w), np.uint8)), source, long_text))
    _, sizes = typeset.typeset(FONTS, bubble_page(small + [big], size=(1000, 920)), bubbles)
    page_size = float(np.median(sizes))
    assert sizes[4] > max(sizes[:4]), f"the roomy bubble should not swim at the page size: {sizes}"
    assert all(0.8 * page_size <= s <= 1.2 * page_size + 1 for s in sizes), f"sizes left the band: {sizes}"


def test_lettering_is_all_caps_by_default_and_mixed_case_on_request(monkeypatch):
    e = (100, 100, 700, 500)
    page = bubble_page([e], size=(800, 600))

    def same_render(case_env: str | None) -> bool:
        if case_env is None:
            monkeypatch.delenv("YOMIKAE_CASE", raising=False)
        else:
            monkeypatch.setenv("YOMIKAE_CASE", case_env)
        lettering = typeset.load_lettering(Settings.from_env().case)
        mixed = typeset.typeset(lettering, page, [bubble_in(e, "Where are you going, Gojo-san?")])[0]
        caps = typeset.typeset(lettering, page, [bubble_in(e, "WHERE are you GOING, gojo-SAN?")])[0]
        return bool((mixed == caps).all())

    assert same_render(None), "default lettering should set the translation in capitals"
    assert not same_render("mixed"), "YOMIKAE_CASE=mixed should keep the translation's casing"


LEADING = {"the", "a", "an", "to", "of", "and", "my", "in", "with", "for"}


def ellipse_region(w: int, h: int) -> typeset.Region:
    mask = np.zeros((h, w), np.uint8)
    cv2.ellipse(mask, (w // 2, h // 2), (w // 2 - 2, h // 2 - 2), 0, 0, 360, 255, -1)
    return typeset.Region(Box(0, 0, w, h), mask > 0, (w // 2, h // 2), on_art=False, dark=False)


def lines_of(text: str, region: typeset.Region, cap: int = 32) -> list[str]:
    _, layout = typeset.fit_bubble(FONTS.dialogue.display(text), (region, region), FONTS.dialogue, cap, names=typeset.lining.names(text))
    return [line for line, _, _ in layout.lines]


def test_no_line_ends_on_a_word_that_leads_into_the_next():
    texts = [
        "I want to go to the beach with all of my friends this summer.",
        "Did you bring the keys to the car and the map of the city?",
        "Give me a minute and I will tell you the whole story of the night.",
    ]
    for text in texts:
        for w, h in ((300, 260), (380, 300), (320, 360)):
            lines = lines_of(text, ellipse_region(w, h))
            enders = [line.split()[-1].lower() for line in lines[:-1]]
            assert not LEADING & set(enders), f"{lines} in {w}x{h}"


def test_a_short_word_is_not_left_alone_on_the_last_line():
    texts = ["Don't you think that's a little too much for you?", "We should probably head back before it gets dark.", "That was the best day of my whole life, I think."]
    for text in texts:
        for w, h in ((300, 260), (360, 280), (260, 300)):
            lines = lines_of(text, ellipse_region(w, h))
            last = [ch for ch in lines[-1] if ch.isalnum()]
            assert len(lines) == 1 or len(lines[-1].split()) > 1 or len(last) > 3, f"{lines} in {w}x{h}"


def burst_outline(cx: int, cy: int, rx: int, ry: int, spikes: int = 12, depth: float = 0.35) -> np.ndarray:
    t = np.linspace(0, 2 * np.pi, 2 * spikes, endpoint=False) + np.pi / 2
    r = np.where(np.arange(2 * spikes) % 2 == 0, 1.0, 1.0 - depth)
    return np.stack([cx + rx * r * np.cos(t), cy + ry * r * np.sin(t)], 1).round().astype(np.int32)


def dialogue_and_one_more(last: str, source: str, burst: bool) -> tuple[np.ndarray, list[Bubble], np.ndarray]:
    ellipses = [(40 + i * 280, 60, 280 + i * 280, 400) for i in range(4)]
    page = bubble_page(ellipses[:3] if burst else ellipses).copy()
    pts = burst_outline(960, 230, 130, 170)
    if burst:
        cv2.fillPoly(page, [pts], (255, 255, 255))
        cv2.polylines(page, [pts], True, (0, 0, 0), 4)
    interior = np.zeros(page.shape[:2], np.uint8)
    cv2.fillPoly(interior, [pts], 1)
    texts = ["Where are you going now?", "I told you to wait here.", "We promised, didn't we?", last]
    sources = ["どこへ行くつもり", "待ってと言った", "約束したでしょう", source]
    return page, [bubble_in(e, t, s) for e, t, s in zip(ellipses, texts, sources)], interior > 0


def test_a_line_in_a_spiky_burst_is_set_as_a_shout_bigger_than_the_dialogue():
    page, bubbles, _ = dialogue_and_one_more("I'll get you for this!", "覚えてろ", burst=True)
    _, sizes = typeset.typeset(FONTS, page, bubbles)
    assert sizes[3] >= 1.25 * max(sizes[:3]), sizes


def test_an_ordinary_bubble_stays_dialogue_however_it_ends_or_is_cased():
    for last, source in [("Shut up!!", "うるさいってば！！"), ("What?!!", "なにそれ本当なの！？"), ("STOP IT", "もうやめてよ")]:
        page, bubbles, _ = dialogue_and_one_more(last, source, burst=False)
        _, sizes = typeset.typeset(FONTS, page, bubbles)
        assert sizes[3] <= (1 + typeset.BAND) * min(sizes[:3]), (last, sizes)


def test_bursts_are_told_apart_from_clouds_tails_boxes_and_wobbly_ovals():
    from bubble_shapes import SHOUTS, labeled

    shapes = labeled(seed=0, per_kind=40)
    called = [(kind, typeset.pointed(mask)) for kind, _, mask in shapes]
    assert not [kind for kind, burst in called if burst and kind not in SHOUTS]
    for kind, share in (("burst", 0.95), ("tailed burst", 0.95), ("jagged box", 0.8)):
        hits = [burst for k, burst in called if k == kind]
        assert sum(hits) >= share * len(hits), (kind, sum(hits))


def jagged_box(cx: int, cy: int, rx: int, ry: int) -> np.ndarray:
    tips = [(-0.5, -1, 0, -1.12), (0.4, -1, 0, -1.1), (1, -0.3, 1.1, 0), (1, 0.5, 1.12, 0), (0.2, 1, 0, 1.1), (-1, 0.1, -1.12, 0)]
    corners = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
    points = []
    for i, (x, y) in enumerate(corners):
        points.append((x, y))
        for tx, ty, ox, oy in tips:
            on_edge = (ty == -1 and i == 0) or (tx == 1 and i == 1) or (ty == 1 and i == 2) or (tx == -1 and i == 3)
            if on_edge:
                along = np.array([1, 0]) if ty in (-1, 1) else np.array([0, 1])
                if i in (2, 3):
                    along = -along
                base = np.array([tx, ty], float)
                points += [tuple(base - 0.08 * along), (ox or tx, oy or ty), tuple(base + 0.08 * along)]
    return np.array([(cx + rx * x, cy + ry * y) for x, y in points]).round().astype(np.int32)


def test_a_line_in_a_jagged_box_is_set_as_a_shout():
    ellipses = [(40 + i * 280, 60, 280 + i * 280, 400) for i in range(3)]
    page = bubble_page(ellipses).copy()
    pts = jagged_box(960, 230, 110, 150)
    cv2.fillPoly(page, [pts], (255, 255, 255))
    cv2.polylines(page, [pts], True, (0, 0, 0), 4)
    texts = ["Where are you going now?", "I told you to wait here.", "We promised, didn't we?", "I'll get you for this!"]
    bubbles = [bubble_in(e, t, "覚えてろ") for e, t in zip(ellipses + [(850, 80, 1070, 380)], texts)]
    _, sizes = typeset.typeset(FONTS, page, bubbles)
    assert sizes[3] >= 1.25 * max(sizes[:3]), sizes


def test_calm_text_on_a_flash_background_stays_dialogue():
    ellipses = [(40 + i * 280, 60, 280 + i * 280, 400) for i in range(3)]
    page = bubble_page(ellipses).copy()
    cv2.circle(page, (960, 230), 230, (255, 255, 255), -1)
    for a in np.arange(0, 2 * np.pi, np.pi / 120):
        ray = np.array([np.cos(a), np.sin(a)])
        side = np.array([-ray[1], ray[0]])
        start = 110 + 70 * abs(np.sin(5 * a)) ** 3
        for begin, shade in ((start, 160), (start + 40, 0)):
            wedge = [(960, 230) + begin * ray, (960, 230) + 240 * ray + 3 * side, (960, 230) + 240 * ray - 3 * side]
            cv2.fillPoly(page, [np.round(wedge).astype(np.int32)], (shade, shade, shade))
    texts = ["Where are you going at this hour of the night, all by yourself?", "I told you to wait right here until I came back for you.", "We promised each other, didn't we? Have you forgotten?", "This is going to be a long road ahead... *sigh*"]
    bubbles = [bubble_in(e, t, "先は長いなぁ…はぁ") for e, t in zip(ellipses + [(850, 80, 1070, 380)], texts)]
    _, sizes = typeset.typeset(FONTS, page, bubbles)
    assert sizes[3] <= (1 + typeset.BAND) * max(sizes[:3]), sizes


def test_a_jagged_region_whose_contour_touches_itself_is_scored_without_crashing():
    rows = [
        ".#............",
        ".#............",
        "..#...#.......",
        "..######......",
        "..###...##....",
        "...#..........",
        "...##.#.......",
        "....##........",
        "...###........",
        "...####......#",
        "....####.#.###",
        "...#.#########",
    ]
    mask = np.pad(np.array([[ch == "#" for ch in row] for row in rows]), 1)
    assert not typeset.pointed(mask)


def segmenter_view_of_a_burst(pts: np.ndarray, block: TextBlock, hole: bool) -> Balloon:
    hull = np.zeros((1100, 1100), np.uint8)
    cv2.fillPoly(hull, [cv2.convexHull(pts)], 1)
    ys, xs = np.nonzero(hull)
    box = Box(int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    mask = hull[box.y0 : box.y1, box.x0 : box.x1] > 0
    if hole:
        mb = block.mask_box
        mask[mb.y0 - box.y0 : mb.y1 - box.y0, mb.x0 - box.x0 : mb.x1 - box.x0] &= block.mask == 0
    return Balloon(box, mask, 0.6)


def test_a_segmenter_mask_that_spills_past_a_burst_or_misses_its_lettering_neither_shrinks_nor_strays():
    page = np.full((1100, 1100, 3), 255, np.uint8)
    pts = burst_outline(550, 550, 300, 260, spikes=9, depth=0.45)
    cv2.polylines(page, [pts], True, (0, 0, 0), 4)
    interior = np.zeros((1100, 1100), np.uint8)
    cv2.fillPoly(interior, [pts], 1)
    interior = cv2.erode(interior, np.ones((5, 5), np.uint8)) > 0
    block = glyph_block(460, 400, 3, 60, 300, 10)
    for text in ["You rude personnnn!!!", "What is 'Magai'?! What on earth does it mean, Imhotep-sama?!"]:
        traced = typeset.typeset(FONTS, page, [Bubble(block, "この無礼者ーっ！！", text)])[1][0]
        assert traced >= 40, (text, traced)
        for hole in (False, True):
            bubble = Bubble(block, "この無礼者ーっ！！", text, segmenter_view_of_a_burst(pts, block, hole))
            rendered, sizes = typeset.typeset(FONTS, page, [bubble])
            assert outside(rendered, page, interior) == 0, (text, hole)
            assert sizes[0] == traced, (text, hole, sizes, traced)


def split_words(lines: list[str]) -> list[str]:
    return [a[:-1].split()[-1] + b.split()[0] for a, b in zip(lines, lines[1:]) if a.endswith("-") and a[:-1].split()]


def test_contractions_and_names_are_never_hyphenated():
    skinny = ellipse_region(64, 520)
    for text in ["Shouldn't you couldn't've wouldn't", "I asked Kiyotaka about everything yesterday"]:
        lines = lines_of(text, skinny)
        assert "".join(lines).replace("-", "").replace(" ", "") == FONTS.dialogue.display(text).replace("-", "").replace(" ", "")
        for word in split_words(lines):
            assert "'" not in word and "KIYOTAKA" not in word, f"{word!r} was split: {lines}"


def outlined(shapes: list[tuple[int, int, int, int]], size=(900, 900)) -> tuple[np.ndarray, np.ndarray]:
    w, h = size
    outer = np.zeros((h, w), np.uint8)
    inner = np.zeros((h, w), np.uint8)
    for cx, cy, ax, ay in shapes:
        cv2.ellipse(outer, (cx, cy), (ax, ay), 0, 0, 360, 255, -1)
        cv2.ellipse(inner, (cx, cy), (ax - 5, ay - 5), 0, 0, 360, 255, -1)
    page = np.full((h, w, 3), 120, np.uint8)
    page[outer > 0] = 0
    page[inner > 0] = 255
    return page, inner > 0


def text_block(columns: list[tuple[int, int, int, int]], size=(900, 900)) -> TextBlock:
    box = Box(min(c[0] for c in columns), min(c[1] for c in columns), max(c[2] for c in columns), max(c[3] for c in columns))
    mask_box = box.expand(20, *size)
    mask = np.zeros((mask_box.h, mask_box.w), np.uint8)
    for x0, y0, x1, y1 in columns:
        mask[y0 - mask_box.y0 : y1 - mask_box.y0, x0 - mask_box.x0 : x1 - mask_box.x0] = 255
    return TextBlock(box, True, mask_box, mask)


def with_glyphs(page: np.ndarray, columns: list[tuple[int, int, int, int]]) -> np.ndarray:
    raw = page.copy()
    for x0, y0, x1, y1 in columns:
        raw[y0:y1:14, x0:x1] = 0
    return raw


def split_and_set(shapes, columns, texts) -> tuple[list[TextBlock], np.ndarray, np.ndarray, np.ndarray]:
    page, interior = outlined(shapes)
    gray = cv2.cvtColor(with_glyphs(page, columns), cv2.COLOR_RGB2GRAY)
    parts = detect.split_by_bubble(gray, text_block(columns))
    bubbles = [Bubble(part, "ごめんね", text) for part, text in zip(parts, texts)]
    rendered = typeset.typeset(FONTS, page, bubbles)[0]
    return parts, rendered, page, interior


def outside(rendered: np.ndarray, page: np.ndarray, interior: np.ndarray) -> int:
    return int((ink(rendered, page) & ~interior).sum())


def test_a_block_over_two_touching_bubbles_gets_one_part_per_bubble():
    shapes = [(450, 230, 170, 140), (450, 560, 170, 190)]
    columns = [(500, 150, 525, 310), (450, 150, 475, 310), (400, 150, 425, 310), (500, 470, 525, 660), (450, 470, 475, 660), (400, 470, 425, 660)]
    texts = ["Um... sorry for the weird question, but...", "Is your father coming home soon?"]
    parts, rendered, page, interior = split_and_set(shapes, columns, texts)
    assert len(parts) == 2, [p.box for p in parts]
    marked = ink(rendered, page)
    assert marked[:370].any() and marked[380:].any(), "each bubble holds its own part"
    assert outside(rendered, page, interior) == 0


def test_a_double_lobe_bubble_gets_one_part_per_lobe_and_no_ink_crosses_its_outline():
    shapes = [(560, 260, 110, 170), (380, 520, 190, 200)]
    columns = [(600, 150, 625, 380), (560, 150, 585, 380), (520, 160, 545, 360), (450, 420, 475, 640), (410, 420, 435, 640), (370, 420, 395, 640), (330, 420, 355, 600)]
    texts = ["Sorry... I should have noticed.", "Umisaki has always been the type to keep everything bottled up alone without saying a word."]
    parts, rendered, page, interior = split_and_set(shapes, columns, texts)
    assert len(parts) == 2, [p.box for p in parts]
    marked = ink(rendered, page)
    upper = np.zeros_like(interior)
    cv2.ellipse(upper.view(np.uint8), (560, 230), (80, 110), 0, 0, 360, 1, -1)
    assert (marked & upper).sum() > 200, "the narrow upper lobe is used"
    assert marked[460:].any(), "the wide lower lobe is used"
    assert outside(rendered, page, interior) == 0


def test_stacked_lobes_whose_columns_interleave_side_to_side_each_get_their_own_english():
    shapes = [(450, 250, 150, 150), (450, 560, 170, 170)]
    columns = [(500, 160, 525, 340), (450, 160, 475, 340), (400, 160, 425, 340), (475, 460, 500, 660), (425, 460, 450, 660), (375, 460, 400, 660)]
    texts = ["What are you looking so surprised about?", "Balloons will not float without helium!"]
    parts, rendered, page, interior = split_and_set(shapes, columns, texts)
    assert len(parts) == 2, [p.box for p in parts]
    marked = ink(rendered, page)
    assert marked[:390].any() and marked[410:].any(), "both lobes are lettered"
    assert outside(rendered, page, interior) == 0


def test_text_of_a_nearby_bubble_inside_the_mask_padding_never_becomes_a_part():
    shapes = [(560, 260, 110, 170), (380, 520, 190, 200), (690, 600, 80, 110)]
    columns = [(600, 150, 625, 380), (560, 150, 585, 380), (520, 160, 545, 360), (450, 420, 475, 640), (410, 420, 435, 640), (370, 420, 395, 640), (330, 420, 355, 600)]
    neighbour = [(645, 530, 665, 670), (675, 530, 695, 670)]
    page, _ = outlined(shapes)
    gray = cv2.cvtColor(with_glyphs(page, columns + neighbour), cv2.COLOR_RGB2GRAY)
    box = text_block(columns).box
    mask_box = box.expand(int(min(box.w, box.h) * detect.MASK_PAD_FRAC) + 4, 900, 900)
    mask = np.zeros((mask_box.h, mask_box.w), np.uint8)
    for x0, y0, x1, y1 in columns + neighbour:
        mask[max(y0, mask_box.y0) - mask_box.y0 : min(y1, mask_box.y1) - mask_box.y0, max(x0, mask_box.x0) - mask_box.x0 : min(x1, mask_box.x1) - mask_box.x0] = 255
    parts = detect.split_by_bubble(gray, TextBlock(box, True, mask_box, mask))
    assert len(parts) == 2, [p.box for p in parts]
    assert all(p.box.center[0] < 610 for p in parts), "the nearby bubble keeps only its own block"


def test_a_small_lobe_hung_on_the_side_of_a_big_one_gets_its_own_english():
    shapes = [(380, 520, 170, 220), (540, 380, 120, 120)]
    columns = [(390, 440, 415, 680), (340, 440, 365, 680), (290, 440, 315, 680), (555, 310, 580, 420), (600, 310, 625, 420)]
    assert detect.lobe_marks(outlined(shapes)[1]) is None, "the distance map shows one lobe, so only the notches tell them apart"
    texts = ["No matter how hard I try, the rope will not come loose.", "Hold on!"]
    parts, rendered, page, interior = split_and_set(shapes, columns, texts)
    assert len(parts) == 2, [p.box for p in parts]
    marked = ink(rendered, page)
    assert marked[250:460, 520:700].any() and marked[460:740, 210:450].any(), "both lobes are lettered"
    assert outside(rendered, page, interior) == 0


def test_a_two_kana_lobe_beside_a_long_speech_gets_its_own_english():
    shapes = [(420, 540, 200, 210), (600, 330, 80, 80)]
    columns = [(480, 400, 505, 700), (430, 400, 455, 700), (380, 400, 405, 700), (330, 420, 355, 680), (588, 290, 612, 370)]
    texts = ["I have told you a hundred times not to touch my things without asking.", "Hmph!"]
    parts, rendered, page, interior = split_and_set(shapes, columns, texts)
    assert len(parts) == 2, [p.box for p in parts]
    marked = ink(rendered, page)
    assert marked[250:400, 530:680].any() and marked[400:740, 230:600].any(), "both lobes are lettered"
    assert outside(rendered, page, interior) == 0


def drawn(interior: np.ndarray) -> np.ndarray:
    page = np.full((*interior.shape, 3), 120, np.uint8)
    page[cv2.dilate(interior.astype(np.uint8), np.ones((11, 11), np.uint8)) > 0] = 0
    page[interior] = 255
    return page


def test_an_oval_with_a_tail_or_a_spiky_burst_keeps_its_block_whole():
    columns = [(560, 340, 585, 560), (510, 340, 535, 560), (390, 340, 415, 520), (340, 340, 365, 520)]
    oval = np.zeros((900, 900), np.uint8)
    cv2.ellipse(oval, (450, 450), (230, 190), 0, 0, 360, 1, -1)
    cv2.fillPoly(oval, [np.array([(360, 600), (430, 620), (250, 820)], np.int32)], 1)
    burst = np.zeros((900, 900), np.uint8)
    cv2.fillPoly(burst, [burst_outline(450, 450, 260, 220)], 1)
    for shape in (oval, burst):
        gray = cv2.cvtColor(with_glyphs(drawn(shape > 0), columns), cv2.COLOR_RGB2GRAY)
        assert len(detect.split_by_bubble(gray, text_block(columns))) == 1


def test_a_column_running_through_the_neck_keeps_its_block_whole():
    page, _ = outlined([(450, 250, 90, 160), (450, 560, 140, 170)])
    columns = [(440, 150, 465, 640)]
    gray = cv2.cvtColor(with_glyphs(page, columns), cv2.COLOR_RGB2GRAY)
    assert len(detect.split_by_bubble(gray, text_block(columns))) == 1


def test_lines_in_a_single_irregular_bubble_stay_inside_it():
    cases = [([(450, 450, 300, 160), (330, 330, 150, 150)], Box(390, 300, 470, 460)), ([(450, 300, 300, 120), (250, 520, 120, 200)], Box(400, 220, 480, 380))]
    for shapes, block in cases:
        page, interior = outlined(shapes)
        bubble = Bubble(TextBlock(block, True, block, np.zeros((block.h, block.w), np.uint8)), "どこへ", "Sorry... I should have noticed. Umisaki has always kept everything bottled up alone.")
        rendered = typeset.typeset(FONTS, page, [bubble])[0]
        assert ink(rendered, page).any()
        assert outside(rendered, page, interior) == 0


def test_a_hyphenated_name_in_a_skinny_bubble_breaks_at_its_hyphens_and_stays_inside():
    page, interior = outlined([(450, 450, 48, 190)])
    block = Box(438, 330, 462, 470)
    bubble = Bubble(TextBlock(block, True, block, np.zeros((block.h, block.w), np.uint8)), "な、凪咲姉…っ", "N-Nagisaki-nee...!")
    rendered, sizes = typeset.typeset(FONTS, page, [bubble])
    assert sizes[0] > 0
    assert outside(rendered, page, interior) == 0
    rows = np.flatnonzero(ink(rendered, page).any(1))
    assert rows.max() - rows.min() > 2 * sizes[0], "the name is set on several lines"


def test_inpainting_smudges_inside_a_bubble_do_not_push_its_text_onto_the_art():
    page, interior = outlined([(450, 450, 200, 260)])
    for x in range(330, 600, 70):
        for y in range(260, 680, 70):
            cv2.circle(page, (x + (y // 70 % 2) * 35, y), 6, (90, 90, 90), -1)
    block = Box(420, 330, 480, 570)
    bubble = Bubble(TextBlock(block, True, block, np.full((block.h, block.w), 255, np.uint8)), "どこへ", "If it's this rich, it'd be a waste not to savor every drop...")
    rendered = typeset.typeset(FONTS, page, [bubble])[0]
    cols = np.flatnonzero(ink(rendered, page).any(0))
    assert cols.max() - cols.min() > 160, "the text is set across the bubble, not in the narrow block"
    assert outside(rendered, page, interior) == 0


def test_lettering_falls_back_to_the_bundled_fonts_without_anime_ace(tmp_path):
    from yomikae.config import lettering_fonts

    assert lettering_fonts("upper", tmp_path, {}) == (tmp_path / "ShantellSans-Medium.ttf", tmp_path / "Bangers-Regular.ttf")
    (tmp_path / "animeace2_reg.ttf").write_bytes(b"")
    assert lettering_fonts("upper", tmp_path, {})[0] == tmp_path / "animeace2_reg.ttf"
    custom = {"YOMIKAE_DIALOGUE_FONT": "/x/a.ttf", "YOMIKAE_SHOUT_FONT": "/x/b.ttf"}
    assert lettering_fonts("upper", tmp_path, custom) == (Path("/x/a.ttf"), Path("/x/b.ttf"))
    for path in lettering_fonts("upper", FONTS_DIR, {}):
        assert path.exists(), path


def test_a_bubble_whose_regions_were_trimmed_away_still_letters_instead_of_crashing():
    empty = typeset.Region(Box(0, 0, 40, 40), np.zeros((40, 40), bool), (20, 20), on_art=False, dark=False)
    region, layout = typeset.fit_bubble(FONTS.dialogue.display("Wait!"), (empty, empty), FONTS.dialogue, 32)
    assert layout.lines


def glyph_block(x0: int, y0: int, columns: int, thickness: int, height: int, gap: int) -> TextBlock:
    box = Box(x0, y0, x0 + columns * thickness + (columns - 1) * gap, y0 + height)
    mask = np.zeros((box.h, box.w), np.uint8)
    for i in range(columns):
        mask[:, i * (thickness + gap) : i * (thickness + gap) + thickness] = 255
    return TextBlock(box, True, box, mask)


def test_big_source_lettering_is_set_proportionally_big():
    ellipses = [(40, 100, 560, 800), (640, 100, 1160, 800)]
    page = bubble_page(ellipses)
    small = Bubble(glyph_block(263, 300, 3, 18, 300, 10), "あなたのことは絶対に許さないからね覚悟してよ本当に", "I won't forgive you")
    big = Bubble(glyph_block(830, 300, 2, 60, 300, 20), "絶対に許さないからね", "I won't forgive you")
    _, sizes = typeset.typeset(FONTS, page, [small, big])
    assert sizes[1] >= 0.6 * 60, sizes
    assert sizes[1] >= 1.5 * sizes[0], sizes


def caption_box(right: int = 0) -> tuple[np.ndarray, np.ndarray]:
    page = np.full((800, 1000, 3), 255, np.uint8)
    page[298:303, 298:700] = 0
    page[518:523, 298:700] = 0
    page[298:523, 298:303] = 0
    page[298:523, 698:703] = right or 0
    if right:
        page[298:523, 698:703] = 255
        page[298:523, 700] = right
    interior = np.zeros((800, 1000), bool)
    interior[303:518, 303:698] = True
    return page, interior


def ink_center(rendered: np.ndarray, page: np.ndarray) -> tuple[float, float]:
    ys, xs = np.nonzero(ink(rendered, page))
    return (xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2


def test_a_block_in_a_caption_box_with_art_inside_is_centred_both_ways():
    page, interior = caption_box()
    page[440:500, 303:698] = 60
    block = Box(350, 330, 650, 410)
    bubble = Bubble(TextBlock(block, False, block, np.zeros((block.h, block.w), np.uint8)), "今日は一人で", "Today, a solo session... not tonight.")
    rendered = typeset.typeset(FONTS, page, [bubble])[0]
    cx, cy = ink_center(rendered, page)
    assert abs(cx - 500) <= 6 and abs(cy - 410) <= 6, (cx, cy)
    assert outside(rendered, page, interior) == 0


def test_no_ink_crosses_a_caption_box_whose_side_is_faint():
    page, interior = caption_box(right=235)
    cv2.rectangle(page, (250, 250), (900, 570), (0, 0, 0), 4)
    block = Box(400, 330, 640, 490)
    bubble = Bubble(TextBlock(block, False, block, np.zeros((block.h, block.w), np.uint8)), "今日は一人で", "I'm gonna have a solo session today... no, not tonight.")
    rendered = typeset.typeset(FONTS, page, [bubble])[0]
    assert ink(rendered, page).any()
    assert outside(rendered, page, interior) == 0


def tall_balloon_page(talls: list[tuple[tuple[int, int, int, int], str]]) -> tuple[np.ndarray, np.ndarray, list[Bubble]]:
    neighbours = [((150, 230, 120, 150), "Where are you going now?"), ((150, 620, 120, 150), "I told you to wait here.")]
    page, interior = outlined([shape for shape, _ in neighbours + talls], size=(1000, 900))
    bubbles = [Bubble(glyph_block(cx - 50, cy - 100, 3, 26, 200, 12), "どこへ行くつもりなの", text) for (cx, cy, _, _), text in neighbours]
    for (cx, cy, ax, ay), text in talls:
        column = round(ax * 0.9)
        bubbles.append(Bubble(glyph_block(cx - column // 2, cy - round(ay * 0.72), 1, column, round(ay * 1.44), 0), "気持ち悪い", text))
    return page, interior, bubbles


def test_a_word_is_hyphenated_only_when_it_cannot_fit_whole_at_three_quarters_of_the_cap():
    text = FONTS.dialogue.display("Disgusting.")
    for w in range(60, 300, 12):
        region = ellipse_region(w, 400)
        _, layout = typeset.fit_bubble(text, (region, region), FONTS.dialogue, 32, 24)
        if any(line.endswith("-") for line, _, _ in layout.lines):
            assert all(typeset.try_layout(text, region, FONTS.dialogue, s, False) is None for s in range(24, 33)), (w, layout)


def test_big_source_lettering_does_not_split_a_word_that_fits_whole_at_the_page_size():
    talls = [((500, 450, 75, 300), "Disgusting."), ((750, 450, 75, 300), "Wonderful.")]
    page, interior, bubbles = tall_balloon_page(talls)
    rendered, sizes = typeset.typeset(FONTS, page, bubbles)
    assert outside(rendered, page, interior) == 0
    assert max(sizes[2:]) <= 1.2 * max(sizes[:2]), sizes
    assert min(sizes[2:]) >= 0.75 * min(sizes[:2]), sizes


def test_a_word_is_split_at_most_once():
    for text in ["Wonderful.", "Disgusting.", "Absolutely not.", "Unbelievable!"]:
        for w in (50, 70, 90):
            lines = lines_of(text, ellipse_region(w, 500))
            assert sum(line.endswith("-") for line in lines) <= len(text.split()), (text, w, lines)


def test_a_long_word_too_wide_for_a_skinny_balloon_is_hyphenated_before_it_is_shrunk():
    talls = [((500, 450, 36, 170), "Seriously?"), ((750, 450, 36, 170), "Absolutely not.")]
    page, interior, bubbles = tall_balloon_page(talls)
    rendered, sizes = typeset.typeset(FONTS, page, bubbles)
    assert outside(rendered, page, interior) == 0
    assert min(sizes[2:]) >= 0.55 * min(sizes[:2]), sizes


UNSQUEEZED = typeset.Lettering(typeset.Style(FONTS.dialogue.face, True), FONTS.shout)


def hand_drawn_box(page: np.ndarray, interior: np.ndarray, x0: int, y0: int, w: int, h: int) -> None:
    corners = np.array([(x0 + 3, y0), (x0 + w - 2, y0 + 2), (x0 + w + 2, y0 + h - 1), (x0 - 2, y0 + h + 1)], np.int32)
    cv2.fillPoly(page, [corners], (255, 255, 255))
    cv2.polylines(page, [corners], True, (0, 0, 0), 3)
    inside = np.zeros(interior.shape, np.uint8)
    cv2.fillPoly(inside, [corners], 1)
    interior |= cv2.erode(inside, np.ones((5, 5), np.uint8)) > 0


def four_koma(width: int, loud: list[str]) -> tuple[np.ndarray, np.ndarray, list[Bubble]]:
    calm = ["Good morning. Did you sleep well last night?", "I made some breakfast for the two of us.", "Let's eat before it gets cold, okay?", "Thanks. Then I'll help myself to some."]
    page = np.full((900, 1000, 3), 140, np.uint8)
    interior = np.zeros(page.shape[:2], bool)
    bubbles = []
    for i, text in enumerate(loud + calm):
        w, h = (width, 260 + 40 * i) if i < len(loud) else (140, 400)
        x0, y0 = 40 + 160 * (i % 6), 40 if i < len(loud) else 470
        if i >= len(loud):
            x0 = 40 + 160 * (i - len(loud))
        hand_drawn_box(page, interior, x0, y0, w, h)
        cx = x0 + w // 2
        bubbles.append(Bubble(glyph_block(cx - 22, y0 + 30, 2, 18, h - 60, 8), "おいしい！本当においしい！！", text))
    return page, interior, bubbles


LOUD_LINES = ["It's delicious! It's really good!!", "Kuraha-san, you're awesome!!", "Hey, call an ambulance!!"]


def test_a_line_too_wide_for_a_narrow_box_is_squeezed_before_it_is_shrunk():
    page, interior, bubbles = four_koma(70, LOUD_LINES)
    rendered, sizes = typeset.typeset(FONTS, page, bubbles)
    _, plain = typeset.typeset(UNSQUEEZED, page, bubbles)
    loud = len(LOUD_LINES)
    assert outside(rendered, page, interior) == 0
    cramped = int(np.argmin(plain[:loud]))
    assert sizes[cramped] >= plain[cramped] + 3, (sizes, plain)
    assert min(sizes[:loud]) >= 0.7 * min(sizes[loud:]), sizes


def test_bubbles_that_fit_at_the_page_size_are_never_squeezed():
    pages = [four_koma(w, LOUD_LINES)[::2] for w in (90, 130)]
    pages.append(dialogue_and_one_more("Shut up!!", "うるさいってば！！", burst=False)[:2])
    for page, bubbles in pages:
        assert (typeset.typeset(FONTS, page, bubbles)[0] == typeset.typeset(UNSQUEEZED, page, bubbles)[0]).all()


def test_the_tight_squeeze_is_tried_only_when_the_first_squeeze_cannot_reach_the_floor():
    dialogue = FONTS.dialogue
    first_only = typeset.Style(dialogue.face, True, dialogue.narrow[:1])
    plain = typeset.Style(dialogue.face, True)
    text = dialogue.display("Whoa! A handmade omelet by Elko-chan!? I'm so happy!!")
    cap, floor = 22, 16
    roomy = ellipse_region(100, 170)
    assert typeset.fit(text, roomy, plain, cap, floor).size < floor
    full, first = typeset.fit(text, roomy, dialogue, cap, floor), typeset.fit(text, roomy, first_only, cap, floor)
    assert (full.size, full.lines) == (first.size, first.lines)
    assert full.size >= floor
    skinny = ellipse_region(90, 170)
    assert typeset.fit(text, skinny, first_only, cap, floor).size < floor
    assert typeset.fit(text, skinny, dialogue, cap, floor).size >= floor


def test_squeezed_lettering_is_narrower_but_as_tall_and_keeps_its_halo():
    def drawn(face: typeset.Fonts) -> tuple[np.ndarray, np.ndarray]:
        page = Image.new("RGB", (400, 120), (128, 128, 128))
        face.draw(page, (40, 80), "WAIT HERE!", 40, (0, 0, 0), 4, (255, 255, 255))
        rgb = np.asarray(page).astype(int).sum(2)
        return rgb < 100, rgb > 700

    plain, squeezed = FONTS.dialogue.face, FONTS.dialogue.narrow[0].face
    (ink_a, halo_a), (ink_b, halo_b) = drawn(plain), drawn(squeezed)
    span = lambda m, axis: np.ptp(np.flatnonzero(m.any(axis)))  # noqa: E731
    assert abs(span(ink_b, 0) - typeset.SQUEEZES[0] * span(ink_a, 0)) <= 4
    assert abs(span(ink_b, 1) - span(ink_a, 1)) <= 1
    assert abs(np.flatnonzero(ink_b.any(0))[0] - np.flatnonzero(ink_a.any(0))[0]) <= 2
    for ink, halo in ((ink_a, halo_a), (ink_b, halo_b)):
        assert span(halo, 0) - span(ink, 0) >= 6 and span(halo, 1) - span(ink, 1) >= 6


LONG_LINE = "Where exactly do you think you are going at this hour of the night?"


def art_page(size=(700, 500), border: int | None = None) -> np.ndarray:
    w, h = size
    page = np.full((h, w, 3), 120, np.uint8)
    if border is not None:
        page[:border] = 255
        page[border : border + 3] = 0
    return page


def oval(page: np.ndarray, cx: int, cy: int, w: int, h: int) -> np.ndarray:
    cv2.ellipse(page, (cx, cy), (w // 2 + 3, h // 2 + 3), 0, 0, 360, (0, 0, 0), -1)
    cv2.ellipse(page, (cx, cy), (w // 2, h // 2), 0, 0, 360, (255, 255, 255), -1)
    inside = np.zeros(page.shape[:2], np.uint8)
    cv2.ellipse(inside, (cx, cy), (w // 2, h // 2), 0, 0, 360, 1, -1)
    return inside > 0


def block_at(cx: int, cy: int, w: int, h: int) -> TextBlock:
    box = Box(cx - w // 2, cy - h // 2, cx + w // 2, cy + h // 2)
    return TextBlock(box, True, box, np.zeros((box.h, box.w), np.uint8))


def drawn_interior(rendered: np.ndarray, x: int, y: int) -> np.ndarray:
    light = (cv2.cvtColor(rendered, cv2.COLOR_RGB2GRAY) >= 200).astype(np.uint8)
    _, labels = cv2.connectedComponents(light, connectivity=4)
    paper = (labels == labels[y, x]).astype(np.uint8)
    contours, _ = cv2.findContours(paper, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    filled = np.zeros_like(paper)
    cv2.drawContours(filled, contours, -1, 1, -1)
    return filled > 0


def test_text_on_art_never_lands_in_a_neighbouring_bubble():
    page = art_page()
    theirs = oval(page, 420, 250, 90, 140)
    bubbles = [Bubble(block_at(330, 250, 20, 50), "どこへ行くつもりなのか教えて", LONG_LINE), Bubble(block_at(420, 250, 30, 80), "…", "")]
    rendered, sizes = typeset.typeset(FONTS, page, bubbles)
    changed = ink(rendered, page)
    assert changed.any() and sizes[0] > 0
    near = cv2.dilate(theirs.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
    assert not (changed & near).any(), "the art text stays out of the neighbouring bubble"


def test_text_in_an_egg_shaped_bubble_sits_near_its_middle_not_down_at_its_widest_row():
    page = Image.new("RGB", (900, 1100), (120, 120, 120))
    t = np.linspace(0, 2 * np.pi, 400)
    ImageDraw.Draw(page).polygon([(450 + 150 * np.cos(v) * (1 + 0.25 * np.sin(v)), 540 + 270 * np.sin(v)) for v in t], fill="white", outline="black", width=4)
    page = np.asarray(page)
    block = Box(400, 410, 500, 670)
    bubble = Bubble(TextBlock(block, True, block, np.zeros((block.h, block.w), np.uint8)), "凪瀬はうちのだから", "Nagise belongs to us")
    rendered = typeset.typeset(FONTS, page, [bubble])[0]
    _, cy = ink_center(rendered, page)
    assert abs(cy - 540) <= 30, cy


def test_each_lobe_of_a_shared_double_bubble_is_eroded_and_kept_as_its_own_part():
    shapes = [(560, 260, 110, 170), (380, 520, 190, 200)]
    columns = [(625, 170, 645, 320), (590, 170, 610, 320), (450, 380, 475, 640), (410, 380, 435, 640), (370, 380, 395, 640), (330, 400, 355, 600)]
    page, interior = outlined(shapes)
    gray = cv2.cvtColor(with_glyphs(page, columns), cv2.COLOR_RGB2GRAY)
    parts = sorted(detect.split_by_bubble(gray, text_block(columns)), key=lambda p: p.box.y0)
    assert len(parts) == 2, [p.box for p in parts]
    ys, xs = np.nonzero(interior)
    balloon = Balloon(Box(int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1), interior[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1], 0.9)
    texts = ["The window was a lot higher than I thought it would be.", "Ouch, that really hurt!"]
    bubbles = [Bubble(p, "ごめんね", t, balloon=balloon, shared=True) for p, t in zip(parts, texts)]
    lobe = np.zeros(interior.shape, np.uint8)
    cv2.ellipse(lobe, (560, 260), (105, 165), 0, 0, 360, 1, -1)
    cv2.ellipse(lobe, (380, 520), (185, 195), 0, 0, 360, 0, -1)
    lobe = lobe > 0
    regions = [typeset.bubble_region(gray, b, (o.block,)) for b, o in zip(bubbles, bubbles[::-1])]
    held = []
    for r in regions:
        full = np.zeros(interior.shape, bool)
        full[r.box.y0 : r.box.y1, r.box.x0 : r.box.x1] = r.mask
        held.append(int((full & lobe).sum()))
    assert held[0] >= 0.75 * lobe.sum(), held
    assert held[1] == 0, held
    rendered = typeset.typeset(FONTS, page, bubbles)[0]
    assert outside(rendered, page, interior) == 0


def test_a_long_word_in_a_bubble_that_tapers_down_sits_at_its_middle_not_up_in_its_widest_rows():
    page = Image.new("RGB", (900, 900), (120, 120, 120))
    outline = [(370, 300), (530, 300), (550, 340), (520, 480), (465, 580), (435, 580), (380, 480), (350, 340)]
    ImageDraw.Draw(page).polygon(outline, fill="white", outline="black", width=4)
    page = np.asarray(page)
    block = Box(435, 340, 465, 540)
    bubble = Bubble(TextBlock(block, True, block, np.zeros((block.h, block.w), np.uint8)), "華鏡丸", "Kakyomaru!!")
    rendered, sizes = typeset.typeset(FONTS, page, [bubble])
    interior = np.zeros(page.shape[:2], np.uint8)
    cv2.fillPoly(interior, [np.array(outline, np.int32)], 1)
    ys = np.nonzero(interior)[0]
    _, cy = ink_center(rendered, page)
    assert abs(cy - ys.mean()) <= 0.06 * (ys.max() - ys.min()), (cy, ys.mean(), sizes)


def test_text_in_a_big_bubble_among_small_ones_grows_until_it_stops_swimming():
    small = [(40 + i * 140, 40, 160 + i * 140, 200) for i in range(4)]
    big = (150, 260, 510, 560)
    page = bubble_page(small + [big], size=(640, 600))
    texts = ["Where are you going now?", "I told you to wait here.", "We promised, didn't we?", "Say that one more time."]
    bubbles = [bubble_in(e, t) for e, t in zip(small, texts)]
    block = Box(290, 320, 370, 500)
    bubbles.append(Bubble(TextBlock(block, True, block, np.zeros((block.h, block.w), np.uint8)), "流しのガンマンをしているシオラと申します", "I'm Siora, a wandering gunman."))
    rendered, sizes = typeset.typeset(FONTS, page, bubbles)
    changed = ink(rendered, page)
    changed[: big[1]] = False
    ys, xs = np.nonzero(changed)
    interior = np.pi * (big[2] - big[0] - 8) * (big[3] - big[1] - 8) / 4
    covered = (xs.max() - xs.min()) * (ys.max() - ys.min()) / interior
    assert covered >= 0.18, f"the text swims in its bubble: {covered:.3f} of it, sizes {sizes}"
    assert sizes[4] <= 1.5 * float(np.median(sizes)) + 1, f"the big bubble jumped too far above the page: {sizes}"
