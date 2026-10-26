from __future__ import annotations

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from yomikae import inpaint
from yomikae.config import FONTS_DIR
from yomikae.model import Box, Bubble, TextBlock

SIZE = (640, 480)
FONT = ImageFont.truetype(str(FONTS_DIR / "Bangers-Regular.ttf"), 72)


@pytest.fixture(scope="module")
def model():
    return inpaint.load_model("cpu")


def hatching() -> Image.Image:
    page = Image.new("L", SIZE, 255)
    draw = ImageDraw.Draw(page)
    for x in range(-SIZE[1], SIZE[0], 9):
        draw.line((x, SIZE[1], x + SIZE[1], 0), fill=0, width=2)
    return page


def screentone() -> Image.Image:
    page = Image.new("L", SIZE, 255)
    draw = ImageDraw.Draw(page)
    for y in range(0, SIZE[1], 8):
        for x in range((y // 8) % 2 * 4, SIZE[0], 8):
            draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=40)
    return page


def letter(page: Image.Image, text: str, at: tuple[int, int], stroke: int) -> Bubble:
    ink = Image.new("L", SIZE, 0)
    ImageDraw.Draw(ink).text(at, text, font=FONT, fill=255)
    ImageDraw.Draw(page).text(at, text, font=FONT, fill=0, stroke_width=stroke, stroke_fill=255)
    ys, xs = np.nonzero(np.asarray(ink))
    box = Box(int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    mask_box = box.expand(int(min(box.w, box.h) * 0.25) + 4, *SIZE)
    seg = np.asarray(ink)[mask_box.y0 : mask_box.y1, mask_box.x0 : mask_box.x1]
    return Bubble(TextBlock(box, False, mask_box, np.where(seg > 127, 255, 0).astype(np.uint8)), text)


def texture(gray: np.ndarray) -> tuple[float, float]:
    return float(gray.mean()), float((gray > 230).mean())


@pytest.mark.parametrize("background", [hatching, screentone])
def test_text_lettered_over_a_texture_is_replaced_by_that_texture(model, background):
    clean = background()
    page = clean.copy()
    bubble = letter(page, "WHOA", (170, 190), stroke=6)
    out = inpaint.inpaint(model, np.asarray(page.convert("RGB")), [bubble])
    gray = cv2.cvtColor(out.image, cv2.COLOR_RGB2GRAY)
    reference = np.asarray(clean)
    lettered = np.abs(np.asarray(page).astype(int) - reference) > 0
    lettered = cv2.dilate(lettered.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    assert out.fills == ["textured"]
    mean, white = texture(gray[lettered])
    ref_mean, ref_white = texture(reference[lettered])
    assert abs(mean - ref_mean) < 25, f"restored mean {mean:.0f} against {ref_mean:.0f}"
    assert white < ref_white + 0.15, f"{white:.0%} of the restored area is blank paper, against {ref_white:.0%} in the texture"
    assert gray[lettered].std() > 0.6 * reference[lettered].std(), "the restored area is flat instead of textured"


def test_text_in_a_plain_white_bubble_takes_the_flat_fill(model):
    page = Image.new("L", SIZE, 140)
    ImageDraw.Draw(page).ellipse((60, 60, 580, 420), fill=255, outline=0, width=5)
    bubble = letter(page, "HEY", (230, 190), stroke=0)
    out = inpaint.inpaint(model, np.asarray(page.convert("RGB")), [bubble])
    assert out.fills == ["flat"]
    box = bubble.block.box
    assert out.image[box.y0 : box.y1, box.x0 : box.x1].min() == 255, "glyph ink is left in the bubble"
