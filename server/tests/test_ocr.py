from __future__ import annotations

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from yomikae import ocr
from yomikae.config import FONTS_DIR
from yomikae.model import Box, TextBlock

SIZE = (900, 520)
LOGO = ImageFont.truetype(str(FONTS_DIR / "Bangers-Regular.ttf"), 110)
JAPANESE = ImageFont.truetype("/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc", 44)


@pytest.fixture(scope="module")
def reader():
    return ocr.load_ocr()


def ink_box(ink: np.ndarray) -> Box:
    ys, xs = np.nonzero(ink)
    return Box(int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)


def test_a_latin_logo_above_japanese_lines_is_left_out_of_the_block(reader):
    page = Image.new("L", SIZE, 255)
    logo, lines = Image.new("L", SIZE, 0), Image.new("L", SIZE, 0)
    ImageDraw.Draw(logo).text((260, 30), "attention!", font=LOGO, fill=255)
    for i, text in enumerate(("なんでも大好きな人向け", "設定はご都合主義ですので", "頭を空っぽにして読んでください")):
        ImageDraw.Draw(lines).text((120, 200 + 80 * i), text, font=JAPANESE, fill=255)
    ink = np.maximum(np.asarray(logo), np.asarray(lines)) > 127
    page.paste(0, mask=Image.fromarray((ink * 255).astype(np.uint8)))
    box = ink_box(ink)
    mask_box = box.expand(12, *SIZE)
    seg = np.where(ink[mask_box.y0 : mask_box.y1, mask_box.x0 : mask_box.x1], 255, 0).astype(np.uint8)
    bubbles, unsure = ocr.read(reader, np.asarray(page.convert("RGB")), [TextBlock(box, False, mask_box, seg)])
    logo_bottom = ink_box(np.asarray(logo) > 127).y1
    read = bubbles + unsure
    assert len(read) == 1 and ocr.is_japanese_text(read[0].source), [b.source for b in read]
    assert "attention" not in read[0].source.lower()
    assert read[0].block.box.y0 >= logo_bottom, f"block starts at y={read[0].block.box.y0}, inside the logo that ends at y={logo_bottom}"
    assert not (read[0].block.mask[: logo_bottom - mask_box.y0] > 0).any(), "the logo is still in the block's text mask"
