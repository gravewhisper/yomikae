from __future__ import annotations

import random

import numpy as np
import pytest
from make_test_page import FONT_PATH, draw_line_art, draw_screentone, draw_vertical_text
from PIL import Image, ImageDraw, ImageFont

from yomikae import detect, ocr
from yomikae.config import DETECTOR_PATH

PAGE = (908, 1280)


@pytest.fixture(scope="module")
def detector():
    return detect.load_session(DETECTOR_PATH)


@pytest.fixture(scope="module")
def reader():
    return ocr.load_ocr()


def read_page(detector, reader, img: Image.Image) -> list[ocr.Bubble]:
    image = np.asarray(img)
    bubbles, unsure = ocr.read(reader, image, detect.detect(detector, image))
    return bubbles + unsure


def inside(bubble: ocr.Bubble, box: tuple[int, int, int, int]) -> bool:
    x, y = bubble.block.box.center
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def test_a_short_interjection_in_a_small_attached_lobe_is_read(detector, reader):
    img = Image.new("RGB", PAGE, "white")
    draw = ImageDraw.Draw(img)
    draw_screentone(img, (0, 0, *PAGE), random.Random(5))
    draw.ellipse((200, 600, 420, 900), fill="white", outline="black", width=4)
    draw.ellipse((330, 540, 430, 660), fill="white", outline="black", width=4)
    draw.ellipse((204, 604, 416, 896), fill="white")
    font = ImageFont.truetype(FONT_PATH, 30)
    draw_vertical_text(img, (250, 650), ["おじさんの", "傘忘れて"], font)
    draw_vertical_text(img, (365, 560), ["あっ"], font)
    lobe = (330, 540, 430, 640)
    read = read_page(detector, reader, img)
    assert any("傘" in b.source for b in read), [b.source for b in read]
    assert [b.source for b in read if inside(b, lobe)] == ["あっ"], [b.source for b in read]


def test_a_short_sound_on_art_beside_a_bubble_is_still_skipped(detector, reader):
    img = Image.new("RGB", PAGE, "white")
    draw = ImageDraw.Draw(img)
    draw_screentone(img, (0, 0, *PAGE), random.Random(5))
    draw.ellipse((200, 600, 420, 900), fill="white", outline="black", width=4)
    font = ImageFont.truetype(FONT_PATH, 30)
    draw_vertical_text(img, (250, 650), ["おじさんの", "傘忘れて"], font)
    draw.rectangle((440, 600, 480, 680), fill="white")
    draw_vertical_text(img, (445, 605), ["はっ"], font)
    sound = (420, 580, 520, 700)
    blocks = detect.detect(detector, np.asarray(img))
    assert any(inside(ocr.Bubble(block=b, source=""), sound) for b in blocks), "the detector did not see the sound, so this test would prove nothing"
    read = read_page(detector, reader, img)
    assert any("傘" in b.source for b in read), [b.source for b in read]
    assert not [b.source for b in read if inside(b, sound)], [b.source for b in read]


def test_a_small_boxed_caption_over_hatching_is_read(detector, reader):
    img = Image.new("RGB", PAGE, "white")
    draw = ImageDraw.Draw(img)
    draw_line_art(img, (0, 0, *PAGE), random.Random(3))
    box = (60, 60, 300, 205)
    draw.rectangle(box, fill="white", outline="black", width=4)
    for x in range(box[0] + 6, box[2] - 4, 4):
        draw.line((x, box[1] + 6, x, box[3] - 6), fill=(150, 150, 150), width=1)
    font = ImageFont.truetype(FONT_PATH, 24)
    draw.text((100, 90), "今日は一人で", font=font, fill="black")
    draw.text((100, 124), "→抜かない", font=font, fill="black")
    read = [b.source for b in read_page(detector, reader, img) if inside(b, box)]
    assert any("今日は一人" in s for s in read), read


def test_a_two_lobe_bubble_with_a_long_tail_is_split_per_lobe(detector):
    img = Image.new("RGB", PAGE, "white")
    draw = ImageDraw.Draw(img)
    draw_screentone(img, (0, 0, *PAGE), random.Random(5))
    lobes = [(540, 580, 720, 830), (700, 500, 890, 760)]
    for e in lobes:
        draw.ellipse(e, fill="black")
    draw.polygon([(600, 790), (40, 900), (40, 912), (640, 815)], fill="black")
    for x0, y0, x1, y1 in lobes:
        draw.ellipse((x0 + 4, y0 + 4, x1 - 4, y1 - 4), fill="white")
    draw.polygon([(604, 795), (44, 904), (44, 908), (636, 812)], fill="white")
    font = ImageFont.truetype(FONT_PATH, 28)
    draw_vertical_text(img, (580, 620), ["青白くて", "もやしみたい"], font)
    draw_vertical_text(img, (750, 540), ["ちょっとは", "鍛えたら？"], font)
    boxes = sorted(b.box.as_list() for b in detect.detect(detector, np.asarray(img)))
    assert len(boxes) == 2, boxes
    left, right = boxes
    assert left[2] < 700 and right[0] > 700, boxes
