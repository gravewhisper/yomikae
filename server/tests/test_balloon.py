from __future__ import annotations

from dataclasses import replace

import cv2
import numpy as np
import pytest
from conftest import png_bytes
from PIL import Image, ImageDraw, ImageFont

from make_test_page import FONT_PATH, make_page
from test_typeset import caption_box
from yomikae import balloon, detect, ocr, typeset
from yomikae.config import BUBBLE_MODEL_PATH, FONTS_DIR
from yomikae.model import Balloon, Box, Bubble, TextBlock

FONTS = typeset.load_lettering("upper", (FONTS_DIR / "ShantellSans-Medium.ttf", FONTS_DIR / "Bangers-Regular.ttf"))
JAPANESE = ImageFont.truetype(FONT_PATH, 34)


@pytest.fixture(scope="module")
def segmenter():
    return detect.load_session(BUBBLE_MODEL_PATH)


@pytest.fixture(scope="module")
def reader():
    return ocr.load_ocr()


def ellipse_mask(e: tuple[int, int, int, int], shape: tuple[int, int], inset: int = 0) -> np.ndarray:
    im = Image.new("L", (shape[1], shape[0]), 0)
    x0, y0, x1, y1 = e
    ImageDraw.Draw(im).ellipse((x0 + inset, y0 + inset, x1 - inset, y1 - inset), fill=255)
    return np.asarray(im) > 0


def as_balloon(mask: np.ndarray) -> Balloon:
    ys, xs = np.nonzero(mask)
    box = Box(int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    return Balloon(box=box, mask=mask[box.y0 : box.y1, box.x0 : box.x1], score=1.0)


def ink_block(page: np.ndarray, area: Box) -> TextBlock:
    ink = np.zeros(page.shape[:2], bool)
    ink[area.y0 : area.y1, area.x0 : area.x1] = page[area.y0 : area.y1, area.x0 : area.x1].mean(2) < 128
    ys, xs = np.nonzero(ink)
    box = Box(int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    mask_box = box.expand(12, page.shape[1], page.shape[0])
    return TextBlock(box, True, mask_box, (ink[mask_box.y0 : mask_box.y1, mask_box.x0 : mask_box.x1] * 255).astype(np.uint8))


def changed(rendered: np.ndarray, page: np.ndarray) -> np.ndarray:
    return np.abs(rendered.astype(int) - page.astype(int)).sum(2) > 0


def test_the_model_outlines_each_synthetic_balloon(segmenter):
    img, truth = make_page(0)
    rgb = np.asarray(img)
    found = balloon.segment(segmenter, rgb)
    for t in truth:
        drawn = ellipse_mask(tuple(t.ellipse), rgb.shape[:2])
        best = 0.0
        for b in found:
            mask = np.zeros(rgb.shape[:2], bool)
            mask[b.box.y0 : b.box.y1, b.box.x0 : b.box.x1] = b.mask
            best = max(best, (mask & drawn).sum() / (mask | drawn).sum())
        assert best > 0.9, (t.ellipse, best)


def test_a_see_through_box_is_set_inside_the_model_outline():
    w, h = 900, 900
    page = Image.new("RGB", (w, h), (255, 255, 255))
    draw = ImageDraw.Draw(page)
    for d in range(-h, w, 6):
        draw.line((d, 0, d + h, h), fill=(90, 90, 90), width=2)
    oval = (220, 160, 680, 740)
    draw.ellipse(oval, outline=(0, 0, 0), width=5)
    page = np.asarray(page)
    interior = ellipse_mask(oval, (h, w), inset=6)
    block = Box(430, 330, 470, 570)
    bubble = Bubble(TextBlock(block, True, block, np.full((block.h, block.w), 255, np.uint8)), "どこへ行くつもり", "Where do you think you're going all by yourself?", balloon=as_balloon(interior))
    rendered, sizes = typeset.typeset(FONTS, page, [bubble])
    marked = changed(rendered, page)
    cols = np.flatnonzero(marked.any(0))
    assert cols.max() - cols.min() > 250, "the text spans the oval, not the narrow block"
    assert sizes[0] >= 24
    assert not (marked & ~interior).any(), "no ink outside the oval"


def test_a_balloon_with_no_detected_text_block_is_found_and_translated(client, monkeypatch):
    img, truth = make_page(401)
    monkeypatch.setenv("YOMIKAE_BALLOON_DETECT", "1")
    monkeypatch.setattr(detect, "detect", lambda session, image: [])
    r = client.post("/v1/pages", files={"image": ("p.png", png_bytes(img), "image/png")})
    assert r.status_code == 200, r.text
    page = r.json()
    assert len(page["bubbles"]) == len(truth)
    for t in truth:
        ex0, ey0, ex1, ey1 = t.ellipse
        inside = [b for b in page["bubbles"] if ex0 <= b["box"][0] and ey0 <= b["box"][1] and b["box"][2] <= ex1 and b["box"][3] <= ey1]
        assert len(inside) == 1, (t.text, page["bubbles"])
        assert inside[0]["source"].replace(" ", "")[:3] == t.text[:3] and inside[0]["translation"]


def test_balloons_with_no_detected_text_block_stay_untouched_when_balloon_detection_is_off(client, monkeypatch):
    img, _ = make_page(402)
    monkeypatch.setattr(detect, "detect", lambda session, image: [])
    monkeypatch.setenv("YOMIKAE_BALLOON_DETECT", "0")
    r = client.post("/v1/pages", files={"image": ("p.png", png_bytes(img), "image/png")})
    assert r.status_code == 200, r.text
    assert r.json()["bubbles"] == []


def test_two_touching_balloons_the_model_merged_keep_their_own_regions(reader):
    w, h = 1000, 900
    small, big = (180, 340, 360, 560), (340, 150, 860, 750)
    im = Image.new("RGB", (w, h), (120, 120, 120))
    draw = ImageDraw.Draw(im)
    draw.ellipse(small, fill=(255, 255, 255), outline=(0, 0, 0), width=5)
    draw.ellipse(big, fill=(255, 255, 255), outline=(0, 0, 0), width=5)
    blank = np.asarray(im).copy()
    for i, ch in enumerate("どこへ"):
        draw.text((260, 400 + 40 * i), ch, font=JAPANESE, fill=(0, 0, 0), anchor="mm")
    for col, text in enumerate(("今日はいい", "天気だね")):
        for i, ch in enumerate(text):
            draw.text((720 - 44 * col, 360 + 40 * i), ch, font=JAPANESE, fill=(0, 0, 0), anchor="mm")
    page = np.asarray(im)
    blocks = [ink_block(page, Box(230, 370, 290, 540)), ink_block(page, Box(660, 330, 750, 580))]
    merged = as_balloon(ellipse_mask(small, (h, w), 6) | ellipse_mask(big, (h, w), 6))
    bubbles, _ = ocr.read(reader, page, blocks, [merged])
    assert len(bubbles) == 2, [b.source for b in bubbles]
    texts = {"どこへ": "Where are you going, all by yourself?", "今日は": "The weather is nice today."}
    bubbles = [replace(b, translation=next(v for k, v in texts.items() if b.source.startswith(k))) for b in bubbles]
    lobes = {"どこへ": ellipse_mask(small, (h, w), 6) & ~ellipse_mask(big, (h, w), 0), "今日は": ellipse_mask(big, (h, w), 6)}
    for key, lobe in lobes.items():
        shown = [b if b.source.startswith(key) else replace(b, translation="") for b in bubbles]
        marked = changed(typeset.typeset(FONTS, blank, shown)[0], blank)
        assert marked.any()
        assert not (marked & ~lobe).any(), f"{key} stays on its side of the shared outline"


def test_a_recovered_line_outside_every_balloon_must_read_cleanly_as_dialogue(reader):
    im = Image.new("RGB", (700, 700), (255, 255, 255))
    draw = ImageDraw.Draw(im)
    draw.ellipse((260, 220, 440, 460), outline=(0, 0, 0), width=5)
    for i, ch in enumerate("ガタッ"):
        draw.text((350, 290 + 44 * i), ch, font=JAPANESE, fill=(0, 0, 0), anchor="mm")
    page = np.asarray(im)
    block = ink_block(page, Box(320, 262, 380, 420))
    kept, _ = ocr.read(reader, page, [block], [])
    dropped, _ = ocr.read(reader, page, [replace(block, recovered=True)], [])
    assert [b.source for b in kept] == ["ガタッ"]
    assert dropped == []


def test_a_caption_box_the_model_also_found_keeps_its_tight_frame_margin():
    page, interior = caption_box()
    block = Box(330, 320, 670, 500)
    bubble = Bubble(TextBlock(block, False, block, np.zeros((block.h, block.w), np.uint8)), "今日は一人で", "Today was supposed to be a quiet solo session, but it turned into something else entirely, and now I do not know what to do with myself tonight or tomorrow.")
    plain = typeset.typeset(FONTS, page, [bubble])
    found = typeset.typeset(FONTS, page, [replace(bubble, balloon=as_balloon(interior))])
    assert found[1] == plain[1]
    assert np.array_equal(found[0], plain[0])
