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


def lettered(img: Image.Image, x_right: int, y: int, cols: list[str], font: ImageFont.FreeTypeFont, pitch: int) -> None:
    draw = ImageDraw.Draw(img)
    for i, col in enumerate(cols):
        for j, ch in enumerate(col):
            draw.text((x_right - pitch * i, y + int(font.size * 1.1) * j), ch, font=font, fill="black", anchor="mm", stroke_width=4, stroke_fill="white")


def ringed(draw: ImageDraw.ImageDraw, shapes: list[tuple[int, int, int, int]]) -> None:
    for e in shapes:
        draw.ellipse(e, fill="black")
    for x0, y0, x1, y1 in shapes:
        draw.ellipse((x0 + 4, y0 + 4, x1 - 4, y1 - 4), fill="white")


@pytest.mark.parametrize("tone", [170, 175, 190])
def test_white_edged_columns_in_a_toned_bubble_stay_one_block(detector, tone):
    img = Image.new("RGB", PAGE, "white")
    draw = ImageDraw.Draw(img)
    draw_line_art(img, (0, 0, *PAGE), random.Random(3))
    draw.ellipse((250, 400, 650, 900), fill=(tone, tone, tone), outline="black", width=4)
    lettered(img, 500, 520, ["今日はいい", "天気だね", "早く帰ろう", "よ"], ImageFont.truetype(FONT_PATH, 30), 40)
    boxes = [b.box.as_list() for b in detect.detect(detector, np.asarray(img)) if b.box.x0 < 650 and b.box.x1 > 250 and b.box.y0 < 900 and b.box.y1 > 400]
    assert len(boxes) == 1, boxes
    assert boxes[0][0] < 400 and boxes[0][2] > 490, boxes


def test_three_touching_lobes_are_split_per_lobe(detector):
    img = Image.new("RGB", PAGE, "white")
    draw = ImageDraw.Draw(img)
    draw_screentone(img, (0, 0, *PAGE), random.Random(5))
    ringed(draw, [(150, 300, 370, 640), (330, 260, 620, 700), (590, 450, 740, 770)])
    font = ImageFont.truetype(FONT_PATH, 28)
    draw_vertical_text(img, (215, 380), ["あの店で", "待ってる"], font)
    draw_vertical_text(img, (470, 350), ["君の名前を教え", "てほしいな", "約束だよ"], font)
    draw_vertical_text(img, (630, 530), ["絶対に"], font)
    boxes = sorted(b.box.as_list() for b in detect.detect(detector, np.asarray(img)))
    assert len(boxes) == 3, boxes
    assert boxes[1][2] < 600 and boxes[2][0] > 600, boxes


def test_two_stacked_lobes_with_a_short_line_at_the_waist_are_split(detector):
    img = Image.new("RGB", PAGE, "white")
    draw = ImageDraw.Draw(img)
    draw_line_art(img, (0, 0, *PAGE), random.Random(3))
    ringed(draw, [(300, 200, 560, 560), (300, 520, 560, 900)])
    font = ImageFont.truetype(FONT_PATH, 30)
    draw_vertical_text(img, (370, 260), ["今日はいい", "天気だね"], font)
    draw_vertical_text(img, (450, 540), ["ねっ"], font)
    draw_vertical_text(img, (340, 600), ["明日また", "会おうね"], font)
    boxes = sorted((b.box.as_list() for b in detect.detect(detector, np.asarray(img))), key=lambda b: b[1])
    assert len(boxes) == 2, boxes
    assert boxes[0][3] < 545 and boxes[1][1] > 500, boxes
